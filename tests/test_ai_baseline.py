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
import logging
import pytest
from datetime import date, timedelta, datetime


# ── Tool Call Helper ──────────────────────────────────────────────


def tool_call(arguments, name="create_habit"):
    """Build a ``chat_with_tools()`` return value matching the new contract.

    ``arguments`` can be any JSON-deserializable value (dict, list, str, etc.).
    Use this helper in *every* test that simulates a successful tool call —
    never pass a bare ``dict`` as ``tools_return``.
    """
    return {"name": name, "arguments": arguments}


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

    # ── Relevance threshold ──────────────────────────────────────

    def test_recommend_no_relevant_candidates_returns_empty(
            self, mock_llm_factory, mock_vector, monkeypatch, client, auth_headers):
        """All candidates below 0.25 → ``([], "vector")``, LLM not called."""
        mock_llm = mock_llm_factory(chat_return='[{"name":"x","reason":"y"}]')
        # All candidates below threshold (0.1 < 0.25)
        low_candidates = [dict(c, score=0.1) for c in FIXED_CANDIDATES[:3]]
        mock_vector.search = lambda query, k=5: low_candidates

        # Guard: these must never be called when gate rejects the query
        def _fail(*a, **kw):
            raise AssertionError("recommendation_prompt or _build_vector_fallback called after gate reject")
        monkeypatch.setattr("app.ai.service.recommendation_prompt", _fail)
        monkeypatch.setattr("app.ai.service._build_vector_fallback", _fail)

        r = client.post("/ai/recommend", json={"goal": "unrelated"},
                        headers=auth_headers)
        assert r.status_code == 200
        data = r.get_json()
        assert data["success"] is True
        assert data["suggestions"] == []
        assert data["source"] == "vector"
        assert len(mock_llm.chat_calls) == 0

    def test_recommend_threshold_boundary(
            self, mock_llm_factory, mock_vector, client, auth_headers):
        """Score 0.25 passes Top-1 gate; low-score candidate (0.24) reaches the LLM."""
        mock_llm = mock_llm_factory(chat_return='[{"name":"晨跑","reason":"ok"}]')
        candidates = [dict(FIXED_CANDIDATES[0], score=0.25),
                      dict(FIXED_CANDIDATES[1], score=0.24)]
        mock_vector.search = lambda query, k=5: candidates
        r = client.post("/ai/recommend", json={"goal": "健康"},
                        headers=auth_headers)
        assert r.status_code == 200
        data = r.get_json()
        assert data["success"] is True
        assert data["source"] == "llm"
        # Low-score candidate (0.24) reached the LLM prompt, not filtered
        assert len(mock_llm.chat_calls) == 1
        prompt = mock_llm.chat_calls[0]["messages"][1]["content"]
        assert "阅读 30 分钟" in prompt, "Low-score candidate (0.24) missing from LLM prompt — would be filtered out by candidate-level gate"


# ── Part 2: NL Habit Parsing (Function Calling) ──────────────────


class TestParseHabit:
    """POST /ai/parse-habit — tool call parsing, prompt contract, current behavior."""

    def test_parse_habit_full_fields(self, mock_llm_factory,
                                     client, auth_headers):
        """All 5 tool fields returned → route returns them as-is."""
        mock_llm_factory(tools_return=tool_call({
            "habit_name": "晨跑",
            "frequency": "Every day this week",
            "time_period": "Morning",
            "icon": "fas fa-running",
            "note": "每天5公里",
        }))
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
        mock_llm_factory(tools_return=tool_call({
            "habit_name": "阅读",
            "frequency": "Every day this week",
            "time_period": "Evening",
        }))
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
        mock_llm = mock_llm_factory(tools_return=tool_call({
            "habit_name": "晨跑",
            "frequency": "Every day this week",
            "time_period": "Morning",
        }))
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

    def test_current_behavior_parse_habit_truthy_non_dict_result(
            self, mock_llm_factory, client, auth_headers):
        """Arguments contains truthy non-dict → ``ToolValidationError``.

        ``tool_call(["not_a_dict"])`` wraps a list in the envelope.
        ``validate_habit_fields()`` checks ``isinstance(result, dict)``
        and rejects the non-dict arguments, returning a specific error.
        """
        mock_llm_factory(tools_return=tool_call(["not_a_dict"]))
        r = client.post("/ai/parse-habit", json={"text": "每天跑步"},
                        headers=auth_headers)
        assert r.status_code == 200
        data = r.get_json()
        assert data["success"] is False
        assert data["message"] == "Tool result must be a dict"

    @pytest.mark.parametrize("tool_result", [
        tool_call({}),
        tool_call([]),
        tool_call(""),
        tool_call(0),
    ])
    def test_current_behavior_parse_habit_falsy_tool_result_cannot_parse(
            self, mock_llm_factory, client, auth_headers, tool_result):
        """Current behavior: falsy arguments → ``Could not parse``.

        ``arguments`` values ``{}``, ``[]``, ``""``, and ``0`` are all falsy,
        so ``parse_habit_text()`` returns ``None`` → Route returns
        ``"Could not parse"``.
        """
        mock_llm_factory(tools_return=tool_result)
        r = client.post("/ai/parse-habit", json={"text": "每天跑步"},
                        headers=auth_headers)
        assert r.status_code == 200
        data = r.get_json()
        assert data["success"] is False
        assert "Could not parse" in data.get("message", "")
        assert "LLM error" not in data.get("message", "")

    # ── Tool/Field validation ─────────────────────────────────────

    def test_parse_habit_unknown_tool_name(self, mock_llm_factory,
                                           client, auth_headers):
        """Unknown tool name → ``ToolValidationError``."""
        mock_llm_factory(tools_return=tool_call(
            {"habit_name": "x", "frequency": "Every day this week",
             "time_period": "Morning"},
            name="bad_tool",
        ))
        r = client.post("/ai/parse-habit", json={"text": "跑步"},
                        headers=auth_headers)
        assert r.status_code == 200
        data = r.get_json()
        assert data["success"] is False
        assert "Unknown tool: bad_tool" in data["message"]

    def test_parse_habit_rejects_missing_habit_name(
            self, mock_llm_factory, client, auth_headers):
        """Missing ``habit_name`` → ``ToolValidationError``."""
        mock_llm_factory(tools_return=tool_call({
            "frequency": "Every day this week",
            "time_period": "Morning",
        }))
        r = client.post("/ai/parse-habit", json={"text": "跑步"},
                        headers=auth_headers)
        assert r.status_code == 200
        data = r.get_json()
        assert data["success"] is False
        assert data["message"] == "habit_name is required"  # DD-TASK-004 explicit rejection

    def test_parse_habit_rejects_non_string_habit_name(
            self, mock_llm_factory, client, auth_headers):
        """Non-string ``habit_name`` → ``ToolValidationError``."""
        mock_llm_factory(tools_return=tool_call({
            "habit_name": 123,
            "frequency": "Every day this week",
            "time_period": "Morning",
        }))
        r = client.post("/ai/parse-habit", json={"text": "测试"},
                        headers=auth_headers)
        assert r.status_code == 200
        data = r.get_json()
        assert data["success"] is False
        assert data["message"] == "habit_name must be a string"

    def test_parse_habit_rejects_empty_habit_name(
            self, mock_llm_factory, client, auth_headers):
        """Whitespace-only ``habit_name`` after trim → ``ToolValidationError``."""
        mock_llm_factory(tools_return=tool_call({
            "habit_name": "   ",
            "frequency": "Every day this week",
            "time_period": "Morning",
        }))
        r = client.post("/ai/parse-habit", json={"text": "测试"},
                        headers=auth_headers)
        assert r.status_code == 200
        data = r.get_json()
        assert data["success"] is False
        assert data["message"] == "habit_name is required"

    def test_parse_habit_rejects_long_habit_name(
            self, mock_llm_factory, client, auth_headers):
        """Oversized ``habit_name`` → ``ToolValidationError``."""
        mock_llm_factory(tools_return=tool_call({
            "habit_name": "x" * 65,
            "frequency": "Every day this week",
            "time_period": "Morning",
        }))
        r = client.post("/ai/parse-habit", json={"text": "测试"},
                        headers=auth_headers)
        assert r.status_code == 200
        data = r.get_json()
        assert data["success"] is False
        assert data["message"] == "habit_name is too long (max 64)"

    def test_parse_habit_rejects_missing_frequency(
            self, mock_llm_factory, client, auth_headers):
        """Missing ``frequency`` → ``ToolValidationError``."""
        mock_llm_factory(tools_return=tool_call({
            "habit_name": "x",
            "time_period": "Morning",
        }))
        r = client.post("/ai/parse-habit", json={"text": "测试"},
                        headers=auth_headers)
        assert r.status_code == 200
        data = r.get_json()
        assert data["success"] is False
        assert data["message"] == "frequency is required"

    def test_parse_habit_rejects_non_string_frequency(
            self, mock_llm_factory, client, auth_headers):
        """Non-string ``frequency`` → ``ToolValidationError``."""
        mock_llm_factory(tools_return=tool_call({
            "habit_name": "x",
            "frequency": 123,
            "time_period": "Morning",
        }))
        r = client.post("/ai/parse-habit", json={"text": "测试"},
                        headers=auth_headers)
        assert r.status_code == 200
        data = r.get_json()
        assert data["success"] is False
        assert data["message"] == "frequency must be a string"

    def test_parse_habit_rejects_invalid_frequency(
            self, mock_llm_factory, client, auth_headers):
        """String ``frequency`` not in allowed set → ``ToolValidationError``."""
        mock_llm_factory(tools_return=tool_call({
            "habit_name": "x",
            "frequency": "bad_freq",
            "time_period": "Morning",
        }))
        r = client.post("/ai/parse-habit", json={"text": "测试"},
                        headers=auth_headers)
        assert r.status_code == 200
        data = r.get_json()
        assert data["success"] is False
        assert data["message"] == "Invalid frequency: bad_freq"

    def test_parse_habit_rejects_missing_time_period(
            self, mock_llm_factory, client, auth_headers):
        """Missing ``time_period`` → ``ToolValidationError``."""
        mock_llm_factory(tools_return=tool_call({
            "habit_name": "x",
            "frequency": "Every day this week",
        }))
        r = client.post("/ai/parse-habit", json={"text": "测试"},
                        headers=auth_headers)
        assert r.status_code == 200
        data = r.get_json()
        assert data["success"] is False
        assert data["message"] == "time_period is required"

    def test_parse_habit_rejects_non_string_time_period(
            self, mock_llm_factory, client, auth_headers):
        """Non-string ``time_period`` → ``ToolValidationError``."""
        mock_llm_factory(tools_return=tool_call({
            "habit_name": "x",
            "frequency": "Every day this week",
            "time_period": 456,
        }))
        r = client.post("/ai/parse-habit", json={"text": "测试"},
                        headers=auth_headers)
        assert r.status_code == 200
        data = r.get_json()
        assert data["success"] is False
        assert data["message"] == "time_period must be a string"

    def test_parse_habit_rejects_invalid_time_period(
            self, mock_llm_factory, client, auth_headers):
        """String ``time_period`` not in allowed set → ``ToolValidationError``."""
        mock_llm_factory(tools_return=tool_call({
            "habit_name": "x",
            "frequency": "Every day this week",
            "time_period": "midnight",
        }))
        r = client.post("/ai/parse-habit", json={"text": "测试"},
                        headers=auth_headers)
        assert r.status_code == 200
        data = r.get_json()
        assert data["success"] is False
        assert data["message"] == "Invalid time_period: midnight"

    def test_parse_habit_rejects_non_string_icon(
            self, mock_llm_factory, client, auth_headers):
        """Non-string ``icon`` → ``ToolValidationError``."""
        mock_llm_factory(tools_return=tool_call({
            "habit_name": "x",
            "frequency": "Every day this week",
            "time_period": "Morning",
            "icon": 42,
        }))
        r = client.post("/ai/parse-habit", json={"text": "测试"},
                        headers=auth_headers)
        assert r.status_code == 200
        data = r.get_json()
        assert data["success"] is False
        assert data["message"] == "icon must be a string"

    def test_parse_habit_rejects_long_icon(
            self, mock_llm_factory, client, auth_headers):
        """Oversized ``icon`` → ``ToolValidationError``."""
        mock_llm_factory(tools_return=tool_call({
            "habit_name": "x",
            "frequency": "Every day this week",
            "time_period": "Morning",
            "icon": "f" * 33,
        }))
        r = client.post("/ai/parse-habit", json={"text": "测试"},
                        headers=auth_headers)
        assert r.status_code == 200
        data = r.get_json()
        assert data["success"] is False
        assert data["message"] == "icon is too long (max 32)"

    def test_parse_habit_rejects_invalid_icon(
            self, mock_llm_factory, client, auth_headers):
        """``icon`` not in ``_VALID_ICONS`` → ``ToolValidationError``."""
        mock_llm_factory(tools_return=tool_call({
            "habit_name": "x",
            "frequency": "Every day this week",
            "time_period": "Morning",
            "icon": "not-an-icon",
        }))
        r = client.post("/ai/parse-habit", json={"text": "测试"},
                        headers=auth_headers)
        assert r.status_code == 200
        data = r.get_json()
        assert data["success"] is False
        assert data["message"] == "Invalid icon: not-an-icon"

    def test_parse_habit_rejects_non_string_note(
            self, mock_llm_factory, client, auth_headers):
        """Non-string ``note`` → ``ToolValidationError``."""
        mock_llm_factory(tools_return=tool_call({
            "habit_name": "x",
            "frequency": "Every day this week",
            "time_period": "Morning",
            "note": 999,
        }))
        r = client.post("/ai/parse-habit", json={"text": "测试"},
                        headers=auth_headers)
        assert r.status_code == 200
        data = r.get_json()
        assert data["success"] is False
        assert data["message"] == "note must be a string"

    def test_parse_habit_rejects_long_note(
            self, mock_llm_factory, client, auth_headers):
        """Oversized ``note`` → ``ToolValidationError``."""
        mock_llm_factory(tools_return=tool_call({
            "habit_name": "x",
            "frequency": "Every day this week",
            "time_period": "Morning",
            "note": "n" * 1025,
        }))
        r = client.post("/ai/parse-habit", json={"text": "测试"},
                        headers=auth_headers)
        assert r.status_code == 200
        data = r.get_json()
        assert data["success"] is False
        assert data["message"] == "note is too long (max 1024)"

    def test_parse_habit_extra_fields_discarded(
            self, mock_llm_factory, client, auth_headers):
        """Extra fields (``user_id``, ``owner_id``, ``is_admin``) are silently discarded."""
        mock_llm_factory(tools_return=tool_call({
            "habit_name": "x",
            "frequency": "Every day this week",
            "time_period": "Morning",
            "user_id": 999,
            "owner_id": "hacker",
            "is_admin": True,
            "extra": "should not appear",
        }))
        r = client.post("/ai/parse-habit", json={"text": "跑步"},
                        headers=auth_headers)
        assert r.status_code == 200
        data = r.get_json()
        assert data["success"] is True
        assert "user_id" not in data["habit"]
        assert "owner_id" not in data["habit"]
        assert "is_admin" not in data["habit"]
        assert "extra" not in data["habit"]
        assert data["habit"]["habit_name"] == "x"

    def test_parse_habit_icon_fas_fa_star_accepted(
            self, mock_llm_factory, client, auth_headers):
        """``icon="fas fa-star"`` is now a valid choice (added to ``ICON_CHOICES``)."""
        mock_llm_factory(tools_return=tool_call({
            "habit_name": "x",
            "frequency": "Every day this week",
            "time_period": "Morning",
            "icon": "fas fa-star",
        }))
        r = client.post("/ai/parse-habit", json={"text": "测试"},
                        headers=auth_headers)
        assert r.status_code == 200
        data = r.get_json()
        assert data["success"] is True
        assert data["habit"]["icon"] == "fas fa-star"

    def test_parse_habit_note_missing_is_ok(
            self, mock_llm_factory, client, auth_headers):
        """Missing ``note`` → accepted (optional field)."""
        mock_llm_factory(tools_return=tool_call({
            "habit_name": "x",
            "frequency": "Every day this week",
            "time_period": "Morning",
        }))
        r = client.post("/ai/parse-habit", json={"text": "测试"},
                        headers=auth_headers)
        assert r.status_code == 200
        data = r.get_json()
        assert data["success"] is True
        # note should not be in the habit or be empty string (depends on LLM)
        # The key assertion is that it doesn't crash

    def test_parse_habit_does_not_write_to_db(
            self, mock_llm_factory, client, auth_headers, app):
        """Successful parse returns a valid habit draft but does NOT write to DB."""
        from app.extensions import db
        from app.models import Habit
        with app.app_context():
            count_before = db.session.query(Habit).count()

        mock_llm_factory(tools_return=tool_call({
            "habit_name": "晨跑",
            "frequency": "Every day this week",
            "time_period": "Morning",
        }))
        r = client.post("/ai/parse-habit", json={"text": "每天早上跑步"},
                        headers=auth_headers)
        assert r.status_code == 200
        payload = r.get_json()
        assert payload["success"] is True
        assert "habit" in payload
        assert payload["habit"]["habit_name"] == "晨跑"

        with app.app_context():
            count_after = db.session.query(Habit).count()
        assert count_after == count_before

    def test_validation_failure_zero_writes(
            self, mock_llm_factory, client, auth_headers, app):
        """Validation failure returns expected error and does NOT create any Habit."""
        from app.extensions import db
        from app.models import Habit
        with app.app_context():
            count_before = db.session.query(Habit).count()

        mock_llm_factory(tools_return=tool_call({
            "frequency": "Every day this week",  # missing habit_name
            "time_period": "Morning",
        }))
        r = client.post("/ai/parse-habit", json={"text": "跑步"},
                        headers=auth_headers)
        assert r.status_code == 200
        payload = r.get_json()
        assert payload["success"] is False
        assert "habit_name is required" in payload["message"]

        with app.app_context():
            count_after = db.session.query(Habit).count()
        assert count_after == count_before


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


# ── Part 5: Reliability & Logging (DD-TASK-006) ──────────────────


class TestAIFallbackLogging:
    """caplog assertions on privacy-safe AI fallback events."""

    def test_recommend_malformed_json_logs_fallback(self, mock_llm_factory,
                                                    mock_vector, client,
                                                    auth_headers, caplog):
        """Malformed JSON triggers ai.recommend.fallback log with no user content."""
        mock_llm_factory(chat_return="not json")
        caplog.set_level(logging.INFO)
        r = client.post("/ai/recommend", json={"goal": "健康"},
                        headers=auth_headers)
        assert r.status_code == 200
        found = any("ai.recommend.fallback" in rec.getMessage() for rec in caplog.records)
        assert found, "Expected ai.recommend.fallback in logs"
        for rec in caplog.records:
            msg = rec.getMessage()
            if "ai.recommend" in msg:
                assert "健康" not in msg, "Goal text leaked into log"
                assert "not json" not in msg, "Raw content leaked into log"

    def test_recommend_exception_logs_fallback(self, mock_llm_factory,
                                               mock_vector, client,
                                               auth_headers, caplog):
        """LLM exception logs ai.recommend.fallback."""
        mock_llm_factory(chat_side_effect=RuntimeError("timeout"))
        caplog.set_level(logging.INFO)
        client.post("/ai/recommend", json={"goal": "健康"},
                    headers=auth_headers)
        found = any("llm_exception" in rec.getMessage() for rec in caplog.records)
        assert found, "Expected llm_exception in fallback log"
        for rec in caplog.records:
            msg = rec.getMessage()
            if "llm_exception" in msg:
                assert "timeout" not in msg, "Exception message leaked"

    def test_report_exception_logs_fallback(self, mock_llm_factory,
                                            client, auth_headers, caplog):
        """LLM exception in report logs ai.report.fallback with reason."""
        mock_llm_factory(chat_side_effect=RuntimeError("timeout"))
        caplog.set_level(logging.INFO)
        client.get("/ai/report?type=weekly&tone=coach")
        found = any("ai.report.fallback" in rec.getMessage() for rec in caplog.records)
        assert found, "Expected ai.report.fallback in logs"
        for rec in caplog.records:
            if "ai.report.fallback" in rec.getMessage():
                assert "timeout" not in rec.getMessage(), "Exception text leaked"

    def test_report_empty_response_logs_fallback(self, mock_llm_factory,
                                                  client, auth_headers, caplog):
        """Empty LLM response in report logs ai.report.fallback reason=empty_response."""
        mock_llm_factory(chat_return="")
        caplog.set_level(logging.INFO)
        client.get("/ai/report?type=weekly&tone=coach")
        found = any("empty_response" in rec.getMessage() for rec in caplog.records)
        assert found, "Expected empty_response in fallback log"

    def test_parse_rejection_logs_tool_validation(self, mock_llm_factory,
                                                   client, auth_headers, caplog):
        """Missing habit_name triggers ai.parse.rejected log, user text not in log."""
        mock_llm_factory(tools_return=tool_call({
            "frequency": "Every day this week",
            "time_period": "Morning",
        }))
        caplog.set_level(logging.INFO)
        r = client.post("/ai/parse-habit", json={"text": "每天早上跑步"},
                        headers=auth_headers)
        assert r.status_code == 200
        assert r.get_json()["success"] is False
        found_rejected = False
        for rec in caplog.records:
            msg = rec.getMessage()
            if "ai.parse.rejected" in msg:
                found_rejected = True
                assert "tool_validation_error" in msg
                assert "ToolValidationError" in msg
                assert "每天早上跑步" not in msg, "User text leaked into log"
                assert "Every day this week" not in msg, "Tool arg value leaked into log"
        assert found_rejected, "Expected ai.parse.rejected log entry"
