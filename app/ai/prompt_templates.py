"""Prompt templates for AI features — recommendation, parsing, and reports."""

# ── Feature 1: RAG Recommendation ─────────────────────────────

RECOMMENDATION_SYSTEM = """You are a professional habit coach. Based on the user's goal and a set of relevant habit templates, suggest 3-5 specific habits they could start tracking.

For each habit, provide:
- name: the habit name (use the template's name or adapt it)
- reason: a short Chinese sentence explaining why this habit helps their goal

Return ONLY a JSON array, no markdown, no explanation:
[
  {"name": "...", "reason": "..."},
  ...
]"""


def recommendation_prompt(goal: str, candidates: list[dict]) -> list[dict]:
    """Build the message list for a recommendation request."""
    templates_text = "\n".join(
        f"- {t['name']} ({t['frequency']}, {t['time_period']}, tags: {' '.join(t['tags'])})"
        for t in candidates
    )
    user_msg = (
        f"User's goal: {goal}\n\n"
        f"Relevant habit templates:\n{templates_text}\n\n"
        "Please select and adapt 3-5 habits from the above templates. "
        "If the user's goal is in Chinese, respond in Chinese."
    )
    return [
        {"role": "system", "content": RECOMMENDATION_SYSTEM},
        {"role": "user", "content": user_msg},
    ]


# ── Feature 2: NL Habit Parsing (Function Calling) ───────────

PARSE_HABIT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "create_habit",
            "description": "Parse a natural-language habit description into structured fields",
            "parameters": {
                "type": "object",
                "properties": {
                    "habit_name": {
                        "type": "string",
                        "description": "Short habit name, e.g. '跑步', '阅读'",
                    },
                    "frequency": {
                        "type": "string",
                        "description": "One of: 'Once a week', 'Twice a week', '3 times a week', '4 times a week', '5 times a week', '6 times a week', 'Every day this week'",
                    },
                    "time_period": {
                        "type": "string",
                        "description": "One of: 'Any time', 'After waking up', 'Morning', 'Noon', 'Afternoon', 'Evening', 'Before bedtime'",
                    },
                    "icon": {
                        "type": "string",
                        "description": "Font Awesome icon class, e.g. 'fas fa-running', 'fas fa-book'",
                    },
                    "note": {
                        "type": "string",
                        "description": "Optional note about this habit (max 200 chars)",
                    },
                },
                "required": ["habit_name", "frequency", "time_period"],
            },
        },
    }
]

PARSE_HABIT_SYSTEM = "You parse natural-language habit descriptions into structured data. Respond in the same language as the user's input."


def parse_habit_messages(text: str) -> list[dict]:
    """Build the message list for a parse-habit request."""
    return [
        {"role": "system", "content": PARSE_HABIT_SYSTEM},
        {"role": "user", "content": f"Parse this habit description: {text}"},
    ]


# ── Feature 3: AI Reports ────────────────────────────────────

REPORT_SYSTEM = {
    "coach": "You are a motivational habit coach. Be encouraging but honest. Focus on progress, not perfection. Use an uplifting tone.",
    "friend": "You are a supportive friend checking in. Be warm, casual, and relatable. Use everyday language.",
    "analyst": "You are a data analyst reviewing habit metrics. Be objective and data-driven. Highlight patterns and suggest improvements based on numbers.",
}


def report_prompt(stats: dict, period: str = "weekly", tone: str = "coach") -> list[dict]:
    """Build the message list for a report generation request.

    *stats* should contain: ``total_habits``, ``total_checkins``, ``completion_rate``,
    ``streak``, ``missed_days``, ``top_habit``, ``period`` (actual dates).
    """
    system = REPORT_SYSTEM.get(tone, REPORT_SYSTEM["coach"])
    user = (
        f"Generate a {period} habit report (in the user's language) based on these stats:\n\n"
        f"- Period: {stats.get('period', 'N/A')}\n"
        f"- Total habits tracked: {stats.get('total_habits', 0)}\n"
        f"- Total check-ins: {stats.get('total_checkins', 0)}\n"
        f"- Overall completion rate: {stats.get('completion_rate', 0):.1f}%\n"
        f"- Best streak: {stats.get('streak', 0)} days\n"
        f"- Missed days: {stats.get('missed_days', 0)}\n"
        f"- Top habit: {stats.get('top_habit', 'N/A')}\n\n"
        "Format the report with markdown headings and bullet points. "
        "Include: 1) Summary of the period, 2) What went well, 3) What could improve, "
        "4) Suggestions for next period."
    )
    return [
        {"role": "system", "content": f"{system} Respond in the user's language."},
        {"role": "user", "content": user},
    ]
