"""AI feature tests — recommend, parse-habit, report."""

import pytest


class _UnavailableLLM:
    """Mock LLM that records — and fails the test on — accidental calls.

    ``available=False`` ensures the route takes the fallback path and never
    invokes ``chat()`` / ``chat_with_tools()``.  If a refactor causes the route
    to call either method despite ``available=False``, the fixture teardown
    assertion catches it because the call is recorded.

    Note: we cannot *raise* in ``chat()`` / ``chat_with_tools()`` because the
    production route wraps those calls in ``try/except Exception`` which would
    silently swallow the failure.
    """
    available = False

    def __init__(self):
        self.chat_calls = []
        self.tools_calls = []

    def chat(self, *args, **kwargs):
        self.chat_calls.append({"args": args, "kwargs": kwargs})
        return ""

    def chat_with_tools(self, *args, **kwargs):
        self.tools_calls.append({"args": args, "kwargs": kwargs})
        return None


# Using autouse=True on every test in this module guarantees isolation
# regardless of execution order or terminal environment variables.
@pytest.fixture(autouse=True)
def _offline_guard(monkeypatch):
    """Isolate every old AI test from real LLM / vector-store dependencies.

    Patches two Route-level imports:

    1. ``get_llm()`` → ``_UnavailableLLM`` instance (records calls)
    2. ``HabitVectorStore`` → simple stub (avoids sentence-transformers)

    On teardown, asserts that no LLM calls snuck through — this catches
    regressions even when the production route's ``except Exception``
    would otherwise swallow the evidence.

    Applied via ``autouse=True`` so *every* test in this module inherits the
    protection, regardless of test order or terminal env variables.
    """
    llm = _UnavailableLLM()

    # ── LLM guard ──────────────────────────────────────────────────
    monkeypatch.setattr(
        "app.blueprints.ai.routes.get_llm",
        lambda: llm,
    )

    # ── Vector-store guard ─────────────────────────────────────────
    FIXED = [
        {"name": "早起", "frequency": "Every day this week",
         "time_period": "After waking up", "icon": "fas fa-sun",
         "category": "health", "tags": ["morning"], "score": 0.85},
        {"name": "阅读", "frequency": "Every day this week",
         "time_period": "Evening", "icon": "fas fa-book",
         "category": "learning", "tags": ["read"], "score": 0.72},
    ]

    class _MockStore:
        def search(self, query, k=5):
            return FIXED[:k]

    monkeypatch.setattr(
        "app.blueprints.ai.routes.HabitVectorStore",
        lambda: _MockStore(),
    )

    yield

    # Teardown guard: routes must not call chat/chat_with_tools when available=False
    assert llm.chat_calls == [], (
        f"Offline fallback test unexpectedly called LLM chat() {llm.chat_calls}"
    )
    assert llm.tools_calls == [], (
        f"Offline fallback test unexpectedly called LLM chat_with_tools() "
        f"{llm.tools_calls}"
    )


class TestAIRecommend:
    def test_recommend_vector_fallback(self, client, auth_headers):
        """Without LLM key, should return vector search results."""
        r = client.post('/ai/recommend', json={'goal': 'get healthier'}, headers=auth_headers)
        assert r.status_code == 200
        data = r.get_json()
        assert data['success'] is True
        assert len(data['suggestions']) >= 1
        assert data['source'] == 'vector'

    def test_recommend_empty_goal(self, client, auth_headers):
        r = client.post('/ai/recommend', json={'goal': ''}, headers=auth_headers)
        assert r.status_code == 200
        assert r.get_json()['success'] is False

    def test_recommend_chinese_goal(self, client, auth_headers):
        r = client.post('/ai/recommend', json={'goal': '想减肥塑形'}, headers=auth_headers)
        assert r.status_code == 200
        data = r.get_json()
        assert data['success'] is True
        assert len(data['suggestions']) >= 1


class TestAIParseHabit:
    def test_parse_without_llm_key(self, client, auth_headers):
        """Without LLM key, should return a graceful message."""
        r = client.post('/ai/parse-habit', json={'text': '每天早上跑步'}, headers=auth_headers)
        assert r.status_code == 200
        data = r.get_json()
        assert data['success'] is False
        assert 'LLM not configured' in data['message']

    def test_parse_empty_text(self, client, auth_headers):
        r = client.post('/ai/parse-habit', json={'text': ''}, headers=auth_headers)
        assert r.status_code == 200
        assert r.get_json()['success'] is False


class TestAIReport:
    def test_weekly_report(self, client, auth_headers):
        r = client.get('/ai/report?type=weekly&tone=coach')
        assert r.status_code == 200

    def test_monthly_report(self, client, auth_headers):
        r = client.get('/ai/report?type=monthly&tone=friend')
        assert r.status_code == 200

    def test_analyst_tone(self, client, auth_headers):
        r = client.get('/ai/report?type=weekly&tone=analyst')
        assert r.status_code == 200

    def test_report_with_data(self, client, auth_headers):
        """Report page when user has habits and check-ins."""
        client.post('/habits/new', json={
            'habit_name': 'Run', 'icon': 'fas fa-running',
            'frequency': 'Every day', 'time_period': 'Morning',
        }, headers=auth_headers)
        r = client.get('/ai/report?type=weekly&tone=coach')
        assert r.status_code == 200
