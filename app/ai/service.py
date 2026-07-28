"""AI service layer — route orchestration, prompt building, LLM calling, fallback.

Separates AI use-case logic from HTTP concerns (request parsing, auth,
response rendering).  Each public function receives its dependencies
(LLM client, vector store) as explicit parameters rather than importing
them — this keeps the existing monkeypatch-based test suite working
without path changes.

DD-TASK-003: AI service boundary refactoring.
"""

import json
from datetime import date, timedelta

from app.extensions import db
from app.ai.prompt_templates import (
    recommendation_prompt,
    parse_habit_messages,
    PARSE_HABIT_TOOLS,
    report_prompt,
)
from app.models import Habit, Record
from app.services.statistics_service import get_habit_stats, get_todo_stats
from app.utils.constants import FREQUENCY_CHOICES, TIME_PERIOD_CHOICES, ICON_CHOICES


# ── Validation constants ─────────────────────────────────────────

_VALID_FREQUENCIES = frozenset(v for v, _ in FREQUENCY_CHOICES if v)
_VALID_TIME_PERIODS = frozenset(v for v, _ in TIME_PERIOD_CHOICES if v)
_VALID_ICONS = frozenset(v for v, _ in ICON_CHOICES)
_ALLOWED_FIELDS = frozenset(["habit_name", "frequency", "time_period", "icon", "note"])


class ToolValidationError(ValueError):
    """Tool arguments failed server-side validation."""


def validate_habit_fields(result: object) -> dict:
    """Validate and normalize an LLM-parsed habit dict.

    Returns a clean dict containing only whitelisted fields.
    Raises ``ToolValidationError`` for missing required fields,
    invalid enum values, or wrong types.
    """
    if not isinstance(result, dict):
        raise ToolValidationError("Tool result must be a dict")

    # 1. Whitelist: drop extra fields
    clean = {k: v for k, v in result.items() if k in _ALLOWED_FIELDS}

    # 2. habit_name — required, string, non-empty, ≤64
    if "habit_name" not in clean:
        raise ToolValidationError("habit_name is required")
    hn = clean["habit_name"]
    if not isinstance(hn, str):
        raise ToolValidationError("habit_name must be a string")
    hn = hn.strip()
    if not hn:
        raise ToolValidationError("habit_name is required")
    if len(hn) > 64:
        raise ToolValidationError("habit_name is too long (max 64)")
    clean["habit_name"] = hn

    # 3. frequency — required, string, enum
    if "frequency" not in clean:
        raise ToolValidationError("frequency is required")
    freq = clean["frequency"]
    if not isinstance(freq, str):
        raise ToolValidationError("frequency must be a string")
    if freq not in _VALID_FREQUENCIES:
        raise ToolValidationError(f"Invalid frequency: {freq}")
    clean["frequency"] = freq

    # 4. time_period — required, string, enum
    if "time_period" not in clean:
        raise ToolValidationError("time_period is required")
    tp = clean["time_period"]
    if not isinstance(tp, str):
        raise ToolValidationError("time_period must be a string")
    if tp not in _VALID_TIME_PERIODS:
        raise ToolValidationError(f"Invalid time_period: {tp}")
    clean["time_period"] = tp

    # 5. icon — optional, string, ≤32, must be valid
    if "icon" in clean:
        ic = clean["icon"]
        if not isinstance(ic, str):
            raise ToolValidationError("icon must be a string")
        if len(ic) > 32:
            raise ToolValidationError("icon is too long (max 32)")
        if ic not in _VALID_ICONS:
            raise ToolValidationError(f"Invalid icon: {ic}")

    # 6. note — optional, string, ≤1024
    if "note" in clean:
        nt = clean["note"]
        if not isinstance(nt, str):
            raise ToolValidationError("note must be a string")
        if len(nt) > 1024:
            raise ToolValidationError("note is too long (max 1024)")

    return clean


# ── Private helpers ───────────────────────────────────────────────


def _parse_json_list(raw: str) -> list[object] | None:
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


def _build_vector_fallback(goal: str, candidates: list[dict]) -> list[dict]:
    """Build the fallback suggestion list from vector-store candidates."""
    return [
        {"name": t["name"], "frequency": t["frequency"],
         "time_period": t["time_period"], "icon": t["icon"],
         "reason": f"Recommended based on your goal: {goal}"}
        for t in candidates
    ]


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


# ── RAG threshold ────────────────────────────────────────────────
# Initial value based on DD-TASK-005A eval dataset (16 cases):
#   Relevant Top-1 cosine min ≈ 0.407
#   Unrelated Top-1 cosine max ≈ 0.181
# 0.25 sits between the two distributions.
# Re-evaluate when knowledge base or embedding model changes.
_RAG_MIN_COSINE_SCORE = 0.25
# ─────────────────────────────────────────────────────────────────


# ── Public use-case functions ────────────────────────────────────


def recommend_habits(goal: str, llm, vector_store) -> tuple[list[object], str]:
    """RAG-powered habit recommendation.

    Returns (suggestions, source) where *source* is "llm" or "vector".
    If the top-1 candidate score is below _RAG_MIN_COSINE_SCORE, returns
    ([], "vector") immediately without calling the LLM.
    Otherwise the full candidate list is passed to the LLM or fallback.
    """
    import math

    candidates = vector_store.search(goal, k=5)

    top_score = candidates[0].get("score") if candidates else None

    if not isinstance(top_score, (int, float)):
        return [], "vector"

    top_score = float(top_score)

    if not math.isfinite(top_score):
        return [], "vector"

    if top_score < _RAG_MIN_COSINE_SCORE:
        return [], "vector"
    if not llm.available:
        return _build_vector_fallback(goal, candidates), "vector"
    try:
        messages = recommendation_prompt(goal, candidates)
        raw = llm.chat(messages, temperature=0.7)
        suggestions = _parse_json_list(raw)
        if suggestions:
            return suggestions, "llm"
    except Exception:
        pass
    return _build_vector_fallback(goal, candidates), "vector"

def parse_habit_text(text: str, llm) -> dict | None:
    """Parse a natural-language habit description into structured fields.

    Returns a validated dict with ``habit_name``, ``frequency``, ``time_period``,
    ``icon``, and optionally ``note``.  ``icon`` defaults to ``"fas fa-star"``
    when missing.

    Returns ``None`` when the LLM is unavailable or makes no tool call.
    Raises ``ToolValidationError`` for invalid fields.
    Raises on LLM API errors (``RuntimeError`` for multi-tool-call, etc.);
    the caller is responsible for catching and returning an appropriate
    JSON error response.
    """
    if not llm.available:
        return None
    try:
        messages = parse_habit_messages(text)
        raw = llm.chat_with_tools(messages, PARSE_HABIT_TOOLS, temperature=0.3)
    except Exception:
        raise
    if raw is None:
        return None
    # Tool name verification
    if raw["name"] != "create_habit":
        raise ToolValidationError(f"Unknown tool: {raw['name']}")
    result = raw["arguments"]
    if not result:
        return None
    # Validate + normalize
    clean = validate_habit_fields(result)
    if not clean.get("icon"):
        clean["icon"] = "fas fa-star"
    return clean


def generate_report_data(user_id: int, report_type: str) -> dict:
    """Gather all statistics for an AI report.

    Complete owner of:
      - *report_type* → *period_days* mapping (weekly→7, monthly→30)
      - Date-range calculation
      - All database queries (habits, records, todos)
      - Streak, missed-days, and completion-rate computation
      - Stats-dict construction

    Does **not** access Flask ``request`` or the LLM.
    Returns a dict with keys: ``period``, ``total_habits``, ``total_checkins``,
    ``completion_rate``, ``streak``, ``missed_days``, ``top_habit``.
    """
    period_days = 7 if report_type == "weekly" else 30
    end = date.today()
    start = end - timedelta(days=period_days)

    habits = db.session.query(Habit).filter_by(user_id=user_id).all()
    total_checkins = db.session.query(Record).join(Habit).filter(
        Habit.user_id == user_id,
        Record.checkin_date >= start,
    ).count()
    total_todos, completed_todos, todo_rate = get_todo_stats(user_id)

    # Streak and top habit
    streak = 0
    top_habit = "N/A"
    habit_stats = get_habit_stats(user_id)
    if habit_stats:
        top_habit = habit_stats[0]["name"]
        all_dates = (
            db.session.query(Record.checkin_date)
            .join(Habit)
            .filter(Habit.user_id == user_id, Record.checkin_date >= start)
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

    # Missed days
    if habits:
        checkin_dates = {
            r.checkin_date for r in
            db.session.query(Record).join(Habit).filter(
                Habit.user_id == user_id,
                Record.checkin_date >= start,
            ).all()
        }
        missed = period_days - len(checkin_dates)
    else:
        missed = period_days

    completion_rate = (total_checkins / (period_days * len(habits)) * 100) if habits and period_days else 0

    return {
        "period": f"{start.isoformat()} to {end.isoformat()}",
        "total_habits": len(habits),
        "total_checkins": total_checkins,
        "completion_rate": completion_rate,
        "streak": streak,
        "missed_days": missed,
        "top_habit": top_habit,
    }


def generate_report_text(stats: dict, report_type: str, tone: str, llm) -> str:
    """Generate an AI report narrative from pre-computed statistics.

    Returns a markdown string.  Falls back to ``_fallback_report(stats)``
    when the LLM is unavailable or the API call fails.
    """
    if not llm.available:
        return _fallback_report(stats)
    try:
        messages = report_prompt(stats, period=report_type, tone=tone)
        return llm.chat(messages, temperature=0.7)
    except Exception:
        return _fallback_report(stats)
