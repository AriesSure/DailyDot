"""AI behavior baseline tests — Mock-based route orchestration, prompt contract, LLM output handling.

DD-TASK-002: Establish reproducible behavior baseline before AI optimization.

Test layers covered:
  - Route orchestration: HTTP → validation → LLM call → response / fallback
  - Prompt input contract: system/user message structure, input embedding
  - LLM output handling: JSON parsing, malformed output, missing fields
  - Data isolation: current user's data only enters prompt

Not covered (delegated to later tasks):
  - Retrieval quality / embedding accuracy → DD-TASK-005
  - Tool execution / parameter validation → DD-TASK-004
"""

import json
import pytest
from datetime import date, timedelta, datetime


# ── Mock Classes ──────────────────────────────────────────────────


class MockLLMClient:
    """Configurable LLM mock for route-level testing.

    Records every ``chat()`` and ``chat_with_tools()`` call for
    prompt-contract assertions.  Does NOT exercise the real OpenAI SDK.
    """

    def __init__(self, *, available=True, chat_return=None,
                 chat_side_effect=None, tools_return=None,
                 tools_side_effect=None):
        self._available = available
        self._chat_return = chat_return
        self._chat_side_effect = chat_side_effect
        self._tools_return = tools_return
        self._tools_side_effect = tools_side_effect
        self.chat_calls = []
        self.tools_calls = []

    @property
    def available(self):
        return self._available

    def chat(self, messages, temperature=0.7):
        self.chat_calls.append({"messages": messages, "temperature": temperature})
        if self._chat_side_effect:
            raise self._chat_side_effect
        return self._chat_return or ""

    def chat_with_tools(self, messages, tools, temperature=0.3):
        self.tools_calls.append({"messages": messages, "tools": tools, "temperature": temperature})
        if self._tools_side_effect:
            raise self._tools_side_effect
        return self._tools_return


FIXED_CANDIDATES = [
    {"name": "晨跑", "frequency": "Every day this week", "time_period": "Morning",
     "icon": "fas fa-running", "category": "fitness", "tags": ["run", "cardio"],
     "score": 0.85},
    {"name": "阅读 30 分钟", "frequency": "Every day this week", "time_period": "Evening",
     "icon": "fas fa-book", "category": "learning", "tags": ["read", "knowledge"],
     "score": 0.72},
    {"name": "喝 8 杯水", "frequency": "Every day this week", "time_period": "Any time",
     "icon": "fas fa-tint", "category": "health", "tags": ["water", "hydration"],
     "score": 0.68},
]


class MockVectorStore:
    """Controllable vector-store stub.

    ``search_calls`` records every invocation so callers can verify
    that the route passed the correct *query* and *k*.
    """

    def __init__(self):
        self.search_calls = []

    def search(self, query, k=5):
        self.search_calls.append({"query": query, "k": k})
        return FIXED_CANDIDATES[:k]


# ── Fixtures ─────────────────────────────────────────────────────


class _FakeDate(date):
    """A ``date`` subclass whose ``today()`` returns a fixed value.

    Used to make report-stat tests deterministic across midnight boundaries.
    The monkey-patch target is ``app.blueprints.ai.routes.date`` (the module
    reference the route code actually uses).
    """
    FIXED_TODAY = date(2026, 7, 27)

    @classmethod
    def today(cls):
        return cls.FIXED_TODAY


@pytest.fixture
def fixed_today(monkeypatch):
    """Replace ``service.date.today()`` with a fixed date (2026-07-27)."""
    monkeypatch.setattr("app.ai.service.date", _FakeDate)


@pytest.fixture
def mock_llm_factory(monkeypatch):
    """Return a factory that creates & injects a MockLLMClient.

    Usage::

        mock_llm = mock_llm_factory(chat_return='[{"name":"x"}]')
    """
    def _factory(**kwargs):
        mock = MockLLMClient(**kwargs)
        monkeypatch.setattr("app.blueprints.ai.routes.get_llm", lambda: mock)
        return mock
    return _factory


@pytest.fixture
def mock_vector(monkeypatch):
    """Inject a MockVectorStore in place of HabitVectorStore."""
    mock = MockVectorStore()
    monkeypatch.setattr("app.blueprints.ai.routes.HabitVectorStore", lambda: mock)
    return mock


# ── Part 1: RAG Recommend ────────────────────────────────────────


class TestRecommend:
    """POST /ai/recommend — route orchestration, fallback, prompt contract."""

    def test_recommend_llm_success(self, mock_llm_factory, mock_vector,
                                   client, auth_headers):
        """LLM returns valid JSON → route returns LLM-sourced suggestions."""
        goal = "健康"
        mock_llm = mock_llm_factory(
            chat_return='[{"name":"晨跑","reason":"提升心肺"}]',
        )
        r = client.post("/ai/recommend", json={"goal": goal},
                        headers=auth_headers)
        assert r.status_code == 200
        data = r.get_json()
        assert data["success"] is True
        assert data["source"] == "llm"
        assert len(data["suggestions"]) == 1
        assert data["suggestions"][0]["name"] == "晨跑"
        assert data["suggestions"][0]["reason"] == "提升心肺"
        assert len(mock_llm.chat_calls) == 1
        assert len(mock_vector.search_calls) == 1
        assert mock_vector.search_calls[0]["query"] == goal
        assert mock_vector.search_calls[0]["k"] == 5

    def test_recommend_llm_malformed_json_fallback(self, mock_llm_factory,
                                                    mock_vector, client,
                                                    auth_headers):
        """LLM returns non-JSON → route falls back to vector results."""
        goal = "变得更健康"
        mock_llm = mock_llm_factory(chat_return="not valid json, lol")
        r = client.post("/ai/recommend", json={"goal": goal},
                        headers=auth_headers)
        assert r.status_code == 200
        data = r.get_json()
        assert data["success"] is True
        assert data["source"] == "vector"
        # First fallback item must have all expected fields
        fb = data["suggestions"][0]
        assert fb["name"] == "晨跑"
        assert fb["frequency"] == "Every day this week"
        assert fb["time_period"] == "Morning"
        assert fb["icon"] == "fas fa-running"
        assert goal in fb["reason"]
        # Verify call counts
        assert len(mock_llm.chat_calls) == 1
        assert len(mock_vector.search_calls) == 1
        assert mock_vector.search_calls[0]["query"] == goal
        assert mock_vector.search_calls[0]["k"] == 5

    def test_recommend_llm_empty_array_fallback(self, mock_llm_factory,
                                                  mock_vector, client,
                                                  auth_headers):
        """LLM returns ``[]`` → falls to vector (``if []:`` is falsy)."""
        goal = "健康"
        mock_llm = mock_llm_factory(chat_return="[]")
        r = client.post("/ai/recommend", json={"goal": goal},
                        headers=auth_headers)
        assert r.status_code == 200
        data = r.get_json()
        assert data["success"] is True
        assert data["source"] == "vector"
        assert len(mock_llm.chat_calls) == 1
        assert len(mock_vector.search_calls) == 1
        assert mock_vector.search_calls[0]["query"] == goal
        assert mock_vector.search_calls[0]["k"] == 5

    def test_recommend_llm_exception_fallback(self, mock_llm_factory,
                                               mock_vector, client,
                                               auth_headers):
        """LLM.chat() raises → route falls to vector, does not crash."""
        goal = "健康"
        mock_llm = mock_llm_factory(chat_side_effect=RuntimeError("timeout"))
        r = client.post("/ai/recommend", json={"goal": goal},
                        headers=auth_headers)
        assert r.status_code == 200
        data = r.get_json()
        assert data["success"] is True
        assert data["source"] == "vector"
        assert len(mock_llm.chat_calls) == 1
        assert len(mock_vector.search_calls) == 1
        assert mock_vector.search_calls[0]["query"] == goal
        assert mock_vector.search_calls[0]["k"] == 5

    def test_recommend_prompt_contract(self, mock_llm_factory, mock_vector,
                                       client, auth_headers):
        """Route sends correct system/user messages, includes goal & templates."""
        mock_llm = mock_llm_factory(
            chat_return='[{"name":"晨跑","reason":"好"}]',
        )
        client.post("/ai/recommend", json={"goal": "变得更健康"},
                    headers=auth_headers)
        assert len(mock_llm.chat_calls) == 1
        msgs = mock_llm.chat_calls[0]["messages"]
        assert len(msgs) == 2
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"
        assert "变得更健康" in msgs[1]["content"]
        assert "晨跑" in msgs[1]["content"]
        assert len(mock_llm.chat_calls) == 1
        assert len(mock_vector.search_calls) == 1
        assert mock_vector.search_calls[0]["query"] == "变得更健康"
        assert mock_vector.search_calls[0]["k"] == 5

    def test_recommend_empty_goal_does_not_call_llm_or_vector(
            self, mock_llm_factory, mock_vector, client, auth_headers):
        """Empty goal is rejected before any expensive dependency is invoked."""
        mock_llm = mock_llm_factory()
        r = client.post("/ai/recommend", json={"goal": ""},
                        headers=auth_headers)
        assert r.status_code == 200
        data = r.get_json()
        assert data["success"] is False
        assert "Please describe your goal" in data["message"]
        assert len(mock_llm.chat_calls) == 0
        assert len(mock_vector.search_calls) == 0


# ── Part 2: NL Habit Parsing (Function Calling) ──────────────────


class TestParseHabit:
    """POST /ai/parse-habit — tool call parsing, prompt contract, current behavior."""

    def test_parse_habit_full_fields(self, mock_llm_factory,
                                     client, auth_headers):
        """All 5 tool fields returned → route returns them as-is."""
        mock_llm_factory(tools_return={
            "habit_name": "晨跑",
            "frequency": "Every day this week",
            "time_period": "Morning",
            "icon": "fas fa-running",
            "note": "每天5公里",
        })
        r = client.post("/ai/parse-habit", json={"text": "每天早上跑步"},
                        headers=auth_headers)
        assert r.status_code == 200
        data = r.get_json()
        assert data["success"] is True
        habit = data["habit"]
        assert habit["habit_name"] == "晨跑"
        assert habit["frequency"] == "Every day this week"
        assert habit["time_period"] == "Morning"
        assert habit["icon"] == "fas fa-running"
        assert habit["note"] == "每天5公里"

    def test_parse_habit_missing_icon_defaults(self, mock_llm_factory,
                                                client, auth_headers):
        """Tool response without icon → route applies ``fas fa-star`` default."""
        mock_llm_factory(tools_return={
            "habit_name": "阅读",
            "frequency": "Every day this week",
            "time_period": "Evening",
        })
        r = client.post("/ai/parse-habit", json={"text": "每天晚上阅读"},
                        headers=auth_headers)
        assert r.status_code == 200
        data = r.get_json()
        assert data["success"] is True
        assert data["habit"]["icon"] == "fas fa-star"

    def test_parse_habit_no_tool_call(self, mock_llm_factory,
                                       client, auth_headers):
        """LLM returns ``None`` (no tool call) → route returns parse failure."""
        mock_llm_factory(tools_return=None)
        r = client.post("/ai/parse-habit", json={"text": "每天跑步"},
                        headers=auth_headers)
        assert r.status_code == 200
        data = r.get_json()
        assert data["success"] is False
        assert "Could not parse" in data["message"]

    def test_parse_habit_llm_exception(self, mock_llm_factory,
                                        client, auth_headers):
        """LLM raises → route catches and returns LLM error message."""
        mock_llm_factory(tools_side_effect=ValueError("bad response"))
        r = client.post("/ai/parse-habit", json={"text": "每天跑步"},
                        headers=auth_headers)
        assert r.status_code == 200
        data = r.get_json()
        assert data["success"] is False
        assert "LLM error" in data["message"]

    def test_parse_habit_tool_call_contract(self, mock_llm_factory,
                                             client, auth_headers):
        """Route sends ``PARSE_HABIT_TOOLS``, correct temperature, and input text."""
        mock_llm = mock_llm_factory(tools_return={
            "habit_name": "晨跑",
            "frequency": "Every day this week",
            "time_period": "Morning",
        })
        client.post("/ai/parse-habit", json={"text": "每天早上跑步"},
                    headers=auth_headers)
        assert len(mock_llm.tools_calls) == 1
        tc = mock_llm.tools_calls[0]
        assert tc["temperature"] == 0.3
        assert tc["tools"][0]["function"]["name"] == "create_habit"
        assert "每天早上跑步" in tc["messages"][1]["content"]

    def test_current_behavior_parse_habit_empty_text(
            self, mock_llm_factory, mock_vector, client, auth_headers):
        """Empty text is rejected before LLM is called."""
        mock_llm = mock_llm_factory()
        r = client.post("/ai/parse-habit", json={"text": ""},
                        headers=auth_headers)
        assert r.status_code == 200
        data = r.get_json()
        assert data["success"] is False
        assert "Please enter a habit description" in data["message"]
        assert len(mock_llm.tools_calls) == 0

    def test_current_behavior_tool_missing_habit_name(
            self, mock_llm_factory, client, auth_headers):
        """Current behavior: missing ``habit_name`` → still returns success, no default name injected."""
        mock_llm_factory(tools_return={
            "frequency": "Every day this week",
            "time_period": "Evening",
        })
        r = client.post("/ai/parse-habit", json={"text": "每天晚上阅读"},
                        headers=auth_headers)
        assert r.status_code == 200
        data = r.get_json()
        assert data["success"] is True
        assert "habit_name" not in data["habit"]
        # ⚠ Current baseline: missing field is not rejected server-side.
        # This test should be updated when DD-TASK-004 adds schema validation.

    def test_current_behavior_tool_invalid_frequency(
            self, mock_llm_factory, client, auth_headers):
        """Current behavior: enum-violating ``frequency`` → still accepted."""
        mock_llm_factory(tools_return={
            "habit_name": "测试",
            "frequency": "not_a_valid_frequency",
            "time_period": "Evening",
        })
        r = client.post("/ai/parse-habit", json={"text": "测试"},
                        headers=auth_headers)
        assert r.status_code == 200
        data = r.get_json()
        assert data["success"] is True
        assert data["habit"]["frequency"] == "not_a_valid_frequency"

    def test_current_behavior_tool_wrong_type_habit_name(
            self, mock_llm_factory, client, auth_headers):
        """Current behavior: integer ``habit_name`` → still accepted."""
        mock_llm_factory(tools_return={
            "habit_name": 123,
            "frequency": "Every day this week",
            "time_period": "Evening",
        })
        r = client.post("/ai/parse-habit", json={"text": "测试"},
                        headers=auth_headers)
        assert r.status_code == 200
        data = r.get_json()
        assert data["success"] is True
        assert data["habit"]["habit_name"] == 123

    def test_current_behavior_parse_habit_truthy_non_dict_result(
            self, mock_llm_factory, client, auth_headers):
        """Route receives truthy non-dict tool result → caught as exception (``result.get("icon")`` fails).

        This test covers route-level behaviour when ``chat_with_tools()`` returns
        a non-dict (e.g. a list).  The real ``llm_client.py:56`` parses JSON via
        ``json.loads()`` first; that SDK-level parsing path is tested in DD-TASK-004.
        """
        mock_llm_factory(tools_return=["not_a_dict"])
        r = client.post("/ai/parse-habit", json={"text": "每天跑步"},
                        headers=auth_headers)
        assert r.status_code == 200
        data = r.get_json()
        assert data["success"] is False
        assert "LLM error" in data["message"]

    @pytest.mark.parametrize("tool_result", [
        {},
        [],
        "",
        0,
    ])
    def test_current_behavior_parse_habit_falsy_tool_result_cannot_parse(
            self, mock_llm_factory, client, auth_headers, tool_result):
        """Current behavior: falsy non-None tool results → ``Could not parse``.

        The pre-refactoring route used ``if result:`` to decide success, so
        ``{}``, ``[]``, ``""``, and ``0`` all fell into the "Could not parse"
        branch (not ``LLM error``).  This test preserves that boundary — it
        should be reviewed when DD-TASK-004 adds proper schema validation.
        """
        mock_llm_factory(tools_return=tool_result)
        r = client.post("/ai/parse-habit", json={"text": "每天跑步"},
                        headers=auth_headers)
        assert r.status_code == 200
        data = r.get_json()
        assert data["success"] is False
        assert "Could not parse" in data.get("message", "")
        assert "LLM error" not in data.get("message", "")


# ── Part 3: AI Report ────────────────────────────────────────────


class TestAIReport:
    """GET /ai/report — generation, fallback, prompt contract, data isolation."""

    def test_report_llm_success(self, mock_llm_factory,
                                client, auth_headers):
        """LLM returns markdown → route renders it in the response body."""
        mock_llm_factory(chat_return="## Weekly\nGreat progress!")
        r = client.get("/ai/report?type=weekly&tone=coach")
        assert r.status_code == 200
        assert "Great progress" in r.get_data(as_text=True)

    def test_report_llm_exception_fallback(self, mock_llm_factory,
                                            client, auth_headers):
        """LLM raises → route returns fallback statistical report."""
        mock_llm_factory(chat_side_effect=RuntimeError("timeout"))
        r = client.get("/ai/report?type=weekly&tone=coach")
        assert r.status_code == 200
        body = r.get_data(as_text=True)
        # Must NOT contain the mock's text (it raised, so never returned)
        assert "UNIQUE_MOCK_MARKER" not in body
        # Fallback report has unique indicators
        assert "Weekly Report" in body
        assert "Configure" in body
        assert "LLM_API_KEY" in body
        # User has no habits, so total is 0
        assert "Habits tracked: 0" in body
        assert "Top habit: N/A" in body

    def test_report_no_habits(self, mock_llm_factory,
                              client, auth_headers):
        """User with zero habits → prompt shows zero values, route does not crash."""
        mock_llm = mock_llm_factory(chat_return="No habits yet")
        r = client.get("/ai/report?type=weekly&tone=coach")
        assert r.status_code == 200
        assert len(mock_llm.chat_calls) == 1
        content = mock_llm.chat_calls[0]["messages"][1]["content"]
        # No habits → all values are zero/N/A for a 7-day period
        assert "Total habits tracked: 0" in content
        assert "Total check-ins: 0" in content
        assert "Top habit: N/A" in content
        assert "Missed days: 7" in content  # 7-day period, all days missed

    def test_report_prompt_contract_type_and_tone(
            self, mock_llm_factory, client, auth_headers):
        """Route passes ``period`` to user message and ``tone`` to system message."""
        mock_llm = mock_llm_factory(chat_return="# Report")
        client.get("/ai/report?type=monthly&tone=analyst")
        assert len(mock_llm.chat_calls) == 1
        msgs = mock_llm.chat_calls[0]["messages"]
        assert msgs[0]["role"] == "system"
        # system message contains the "analyst" persona (not the word "analyst" — the persona text)
        assert "data analyst" in msgs[0]["content"]
        assert msgs[1]["role"] == "user"
        # period appears in user message as "monthly", not just in the stat label
        assert "monthly" in msgs[1]["content"]

    def test_report_prompt_contract_user_stats(
            self, mock_llm_factory, fixed_today, client, auth_headers, app):
        """User's habit statistics appear in the LLM prompt with actual values.

        Uses a fixed date (2026-07-27) for deterministic assertions.
        """
        FIXED = _FakeDate.FIXED_TODAY
        # Create a habit + check-in directly in the DB
        from app.extensions import db
        from app.models import Habit, Record, User
        current = db.session.query(User).filter_by(username="testuser").first()
        assert current is not None
        habit = Habit(
            habit_name="测试习惯",
            icon="fas fa-flask",
            frequency="Every day this week",
            time_period="Morning",
            user_id=current.id,
        )
        db.session.add(habit)
        db.session.flush()
        checkin = Record(
            checkin_date=FIXED,
            habit_id=habit.id,
        )
        db.session.add(checkin)
        db.session.commit()

        mock_llm = mock_llm_factory(chat_return="# Report")
        client.get("/ai/report?type=weekly&tone=coach")
        assert len(mock_llm.chat_calls) == 1
        content = mock_llm.chat_calls[0]["messages"][1]["content"]

        # total_habits must be 1 (we created exactly one)
        assert "Total habits tracked: 1" in content
        # total_checkins must be 1 (we made one check-in)
        assert "Total check-ins: 1" in content
        # top_habit must be our habit's name
        assert "Top habit: 测试习惯" in content
        # missed_days: 7-day period minus the 1 day checked in = 6
        assert "Missed days: 6" in content
        # period string is based on the fixed date
        expected_start = (FIXED - timedelta(days=7)).isoformat()
        assert expected_start in content
        assert FIXED.isoformat() in content

    def test_report_data_isolation_other_user(
            self, mock_llm_factory, client, auth_headers, app):
        """Only the current user's data appears in the prompt — other users are invisible."""
        # User A (logged in via auth_headers) creates a habit
        client.post("/habits/new", json={
            "habit_name": "A的秘密习惯",
            "icon": "fas fa-star",
            "frequency": "Every day this week",
            "time_period": "Morning",
        }, headers=auth_headers)

        # Create User B directly in the DB (no login needed)
        from app.extensions import db
        from app.models import User, Habit
        user_b = User(username="user_b", email="b@example.com")
        user_b.set_password("Pass1234")
        db.session.add(user_b)
        db.session.flush()
        habit_b = Habit(
            habit_name="B的独特习惯（不应泄露）",
            icon="fas fa-eye",
            frequency="Every day this week",
            time_period="Evening",
            user_id=user_b.id,
        )
        db.session.add(habit_b)
        db.session.commit()

        # Still logged in as User A — request a report
        mock_llm = mock_llm_factory(chat_return="# Report")
        client.get("/ai/report?type=weekly&tone=coach")
        assert len(mock_llm.chat_calls) == 1
        content = mock_llm.chat_calls[0]["messages"][1]["content"]

        # User A's habit should appear
        assert "A的秘密习惯" in content or "A的秘密" in content
        # User B's habit must NOT appear
        assert "B的独特习惯（不应泄露）" not in content
        assert "B的独特" not in content


# ── Part 4: Helpers + Edge Cases ─────────────────────────────────


class TestParseJsonList:
    """_parse_json_list — markdown fence handling, non-list JSON, empty input."""

    def test_markdown_fence(self):
        from app.ai.service import _parse_json_list
        result = _parse_json_list('```json\n[{"a":1}]\n```')
        assert result == [{"a": 1}]

    def test_bare_json(self):
        from app.ai.service import _parse_json_list
        result = _parse_json_list('[{"a":1}]')
        assert result == [{"a": 1}]

    def test_non_list_json(self):
        from app.ai.service import _parse_json_list
        result = _parse_json_list('{"key":"val"}')
        assert result is None

    def test_empty_string(self):
        from app.ai.service import _parse_json_list
        result = _parse_json_list("")
        assert result is None


class TestRecommendEdgeCases:
    """Characterisation tests: LLM outputs that are structurally invalid.

    These tests document the *current* behaviour, which tolerates
    malformed LLM output.  They should be updated when output-schema
    validation is introduced (DD-TASK-003/004).
    """

    def test_current_behavior_recommend_accepts_missing_reason(
            self, mock_llm_factory, mock_vector, client, auth_headers):
        """LLM returns object without 'reason' → route returns it (no schema check)."""
        mock_llm_factory(chat_return='[{"name":"晨跑"}]')
        r = client.post("/ai/recommend", json={"goal": "健康"},
                        headers=auth_headers)
        assert r.status_code == 200
        data = r.get_json()
        assert data["source"] == "llm"
        assert data["suggestions"][0] == {"name": "晨跑"}

    def test_current_behavior_recommend_accepts_non_object_items(
            self, mock_llm_factory, mock_vector, client, auth_headers):
        """LLM returns list of non-objects → route returns them as-is."""
        mock_llm_factory(chat_return='[123, "abc"]')
        r = client.post("/ai/recommend", json={"goal": "健康"},
                        headers=auth_headers)
        assert r.status_code == 200
        data = r.get_json()
        assert data["source"] == "llm"
        assert data["suggestions"] == [123, "abc"]
