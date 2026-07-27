"""AI blueprint — HTTP layer only: auth, request parsing, response rendering."""

from flask import Blueprint, render_template, jsonify, request
from flask_login import current_user, login_required

from app.ai.vector_store import HabitVectorStore
from app.ai.llm_client import get_llm
from app.ai.service import (
    recommend_habits,
    parse_habit_text,
    generate_report_data,
    generate_report_text,
)
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

    store = HabitVectorStore()
    llm = get_llm()
    suggestions, source = recommend_habits(goal, llm, store)
    return jsonify({"success": True, "suggestions": suggestions, "source": source})


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
    try:
        result = parse_habit_text(text, llm)
    except Exception as exc:
        return jsonify({"success": False, "message": f"LLM error: {exc}"})
    if result is None:
        if not llm.available:
            return jsonify({"success": False, "message": "LLM not configured (set LLM_API_KEY)."})
        return jsonify({"success": False, "message": "Could not parse habit description."})
    return jsonify({"success": True, "habit": result})


# ── Feature 3: AI Report ─────────────────────────────────────


@ai_bp.route("/report")
@login_required
def ai_report():
    """Generate an AI-powered weekly/monthly report."""
    report_type = request.args.get("type", "weekly")
    tone = request.args.get("tone", "coach")

    stats = generate_report_data(current_user.id, report_type)
    llm = get_llm()
    report_md = generate_report_text(stats, report_type, tone, llm)

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
