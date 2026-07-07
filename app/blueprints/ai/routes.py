"""AI blueprint — RAG recommendation, NL parsing, AI reports."""

import json
from datetime import date, timedelta

from flask import Blueprint, render_template, jsonify, request
from flask_login import current_user, login_required

from app.extensions import db
from app.ai.vector_store import HabitVectorStore
from app.ai.llm_client import get_llm
from app.ai.prompt_templates import (
    recommendation_prompt,
    parse_habit_messages,
    PARSE_HABIT_TOOLS,
    report_prompt,
)
from app.services.habit_service import create_habit
from app.services.statistics_service import get_habit_stats, get_daily_checkin_data, get_todo_stats
from app.models import Habit, Record

ai_bp = Blueprint("ai", __name__, url_prefix="/ai")


# ── Feature 1: RAG Habit Recommendation ──────────────────────


@ai_bp.route("/recommend", methods=["POST"])
@login_required
def recommend():
    """Accept a user goal → RAG search → LLM → structured suggestions."""
    data = request.get_json(silent=True) or {}
    goal = (data.get("goal") or "").strip()
    if not goal:
        return jsonify({"success": False, "message": "Please describe your goal."})

    # 1. Vector search
    store = HabitVectorStore()
    candidates = store.search(goal, k=5)

    # 2. LLM generation (with graceful fallback)
    llm = get_llm()
    if llm.available:
        try:
            messages = recommendation_prompt(goal, candidates)
            raw = llm.chat(messages, temperature=0.7)
            suggestions = _parse_json_list(raw)
            if suggestions:
                return jsonify({"success": True, "suggestions": suggestions, "source": "llm"})
        except Exception:
            pass

    # 3. Fallback: return raw vector results
    fallback = [
        {"name": t["name"], "frequency": t["frequency"],
         "time_period": t["time_period"], "icon": t["icon"],
         "reason": f"Recommended based on your goal: {goal}"}
        for t in candidates
    ]
    return jsonify({"success": True, "suggestions": fallback, "source": "vector"}), 200


def _parse_json_list(raw: str) -> list | None:
    """Extract a JSON array from LLM output (handles markdown fences)."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        text = text.rsplit("```", 1)[0]
    text = text.strip()
    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
    except json.JSONDecodeError:
        pass
    return None


# ── Feature 2: Natural-Language Habit Parsing ────────────────


@ai_bp.route("/parse-habit", methods=["POST"])
@login_required
def parse_habit():
    """Parse natural-language text → structured habit fields (Function Calling)."""
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"success": False, "message": "Please enter a habit description."})

    llm = get_llm()
    if not llm.available:
        return jsonify({"success": False, "message": "LLM not configured (set LLM_API_KEY)."})

    try:
        messages = parse_habit_messages(text)
        result = llm.chat_with_tools(messages, PARSE_HABIT_TOOLS, temperature=0.3)
        if result:
            # Ensure icon has a sensible default
            if not result.get("icon"):
                result["icon"] = "fas fa-star"
            return jsonify({"success": True, "habit": result})
        return jsonify({"success": False, "message": "Could not parse habit description."})
    except Exception as e:
        return jsonify({"success": False, "message": f"LLM error: {str(e)}"})


# ── Feature 3: AI Report ─────────────────────────────────────


@ai_bp.route("/report")
@login_required
def ai_report():
    """Generate an AI-powered weekly/monthly report."""
    report_type = request.args.get("type", "weekly")
    tone = request.args.get("tone", "coach")

    # Gather stats
    period_days = 7 if report_type == "weekly" else 30
    start = date.today() - timedelta(days=period_days)
    habits = db.session.query(Habit).filter_by(user_id=current_user.id).all()
    total_checkins = db.session.query(Record).join(Habit).filter(
        Habit.user_id == current_user.id,
        Record.checkin_date >= start,
    ).count()
    total_todos, completed_todos, todo_rate = get_todo_stats(current_user.id)

    # Find best streak and top habit
    streak = 0
    top_habit = "N/A"
    habit_stats = get_habit_stats(current_user.id)
    if habit_stats:
        top_habit = habit_stats[0]["name"]
        # Crude streak: count consecutive days with any check-in
        all_dates = (
            db.session.query(Record.checkin_date)
            .join(Habit)
            .filter(Habit.user_id == current_user.id, Record.checkin_date >= start)
            .distinct()
            .order_by(Record.checkin_date.desc())
            .all()
        )
        if all_dates:
            streak = 1
            for i in range(len(all_dates) - 1):
                diff = (all_dates[i][0] - all_dates[i + 1][0]).days
                if diff == 1:
                    streak += 1
                else:
                    break

    # Count days without check-in
    missed = period_days - len(
        {r.checkin_date for r in
         db.session.query(Record).join(Habit).filter(
             Habit.user_id == current_user.id,
             Record.checkin_date >= start,
         ).all()}
    ) if habits else period_days

    completion_rate = (total_checkins / (period_days * len(habits)) * 100) if habits and period_days else 0

    stats = {
        "period": f"{start.isoformat()} to {date.today().isoformat()}",
        "total_habits": len(habits),
        "total_checkins": total_checkins,
        "completion_rate": completion_rate,
        "streak": streak,
        "missed_days": missed,
        "top_habit": top_habit,
    }

    llm = get_llm()
    if llm.available:
        try:
            messages = report_prompt(stats, period=report_type, tone=tone)
            report_md = llm.chat(messages, temperature=0.7)
        except Exception:
            report_md = _fallback_report(stats)
    else:
        report_md = _fallback_report(stats)

    import markdown
    html_report = markdown.markdown(report_md, extensions=["extra"])

    return render_template(
        "ai_report.html",
        title=f"AI {report_type.capitalize()} Report",
        report=html_report,
        report_type=report_type,
        tone=tone,
        stats=stats,
    )


def _fallback_report(stats: dict) -> str:
    """Generate a plain statistical report when LLM is unavailable."""
    return (
        f"## Weekly Report\n\n"
        f"**Period:** {stats['period']}\n\n"
        f"### Summary\n"
        f"- Habits tracked: {stats['total_habits']}\n"
        f"- Total check-ins: {stats['total_checkins']}\n"
        f"- Completion rate: {stats['completion_rate']:.1f}%\n"
        f"- Best streak: {stats['streak']} days\n"
        f"- Missed days: {stats['missed_days']}\n"
        f"- Top habit: {stats['top_habit']}\n\n"
        "> 💡 Configure `LLM_API_KEY` to get AI-powered analysis and suggestions."
    )
