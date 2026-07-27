"""Direct tests for LLMClient.chat_with_tools() — new contract.

DD-TASK-004: Verifies the real ``chat_with_tools()`` returns
``{"name": str, "arguments": object} | None`` and handles edge cases
(multiple tool calls, malformed JSON) without calling a real API.
"""

import json
from types import SimpleNamespace

import pytest
from app.ai.llm_client import LLMClient


def _make_fake_create(fake_message):
    """Return a callable that fakes ``client.chat.completions.create()``.

    The returned function ignores the SDK parameters and returns a response
    whose ``choices[0].message`` is *fake_message*.
    """
    def fake_create(model, messages, tools, tool_choice, temperature):
        choice = SimpleNamespace(message=fake_message)
        resp = SimpleNamespace(choices=[choice])
        return resp
    return fake_create


@pytest.fixture
def llm():
    """A real LLMClient instance with a patched SDK ``create`` method."""
    client = LLMClient()
    # Initialise the underlying _client so we can patch its method
    _ = client._get_client()
    return client


def test_chat_with_tools_no_tool_call(llm, monkeypatch):
    """No ``tool_calls`` in response → returns ``None``."""
    msg = SimpleNamespace(tool_calls=None)
    monkeypatch.setattr(
        llm._client.chat.completions, "create",
        _make_fake_create(msg),
    )
    result = llm.chat_with_tools(
        [{"role": "user", "content": "hi"}],
        [{"type": "function", "function": {"name": "test"}}],
    )
    assert result is None


def test_chat_with_tools_single_tool_call(llm, monkeypatch):
    """One tool call → returns ``{"name": ..., "arguments": ...}``."""
    function = SimpleNamespace()
    function.name = "create_habit"
    function.arguments = '{"habit_name":"晨跑","frequency":"Every day this week"}'

    tc = SimpleNamespace(function=function)
    msg = SimpleNamespace(tool_calls=[tc])
    monkeypatch.setattr(
        llm._client.chat.completions, "create",
        _make_fake_create(msg),
    )
    result = llm.chat_with_tools(
        [{"role": "user", "content": "每天早上跑步"}],
        [{"type": "function", "function": {"name": "create_habit", "parameters": ...}}],
    )
    assert result is not None
    assert result["name"] == "create_habit"
    assert result["arguments"]["habit_name"] == "晨跑"
    assert result["arguments"]["frequency"] == "Every day this week"


def test_chat_with_tools_multiple_tool_calls_raises(llm, monkeypatch):
    """Two tool calls → raises ``RuntimeError``."""
    tc1 = SimpleNamespace(function=SimpleNamespace(name="a", arguments="{}"))
    tc2 = SimpleNamespace(function=SimpleNamespace(name="b", arguments="{}"))
    msg = SimpleNamespace(tool_calls=[tc1, tc2])
    monkeypatch.setattr(
        llm._client.chat.completions, "create",
        _make_fake_create(msg),
    )
    with pytest.raises(RuntimeError, match="Expected exactly 1 tool call, got 2"):
        llm.chat_with_tools(
            [{"role": "user", "content": "test"}],
            [{"type": "function", "function": {"name": "test"}}],
        )


def test_chat_with_tools_invalid_json_raises(llm, monkeypatch):
    """Non-JSON ``arguments`` string → raises ``json.JSONDecodeError``."""
    function = SimpleNamespace()
    function.name = "test"
    function.arguments = "not valid json"

    tc = SimpleNamespace(function=function)
    msg = SimpleNamespace(tool_calls=[tc])
    monkeypatch.setattr(
        llm._client.chat.completions, "create",
        _make_fake_create(msg),
    )
    with pytest.raises(json.JSONDecodeError):
        llm.chat_with_tools(
            [{"role": "user", "content": "test"}],
            [{"type": "function", "function": {"name": "test"}}],
        )


def test_chat_with_tools_arguments_is_list(llm, monkeypatch):
    """``arguments`` can be a JSON array — client preserves the type."""
    function = SimpleNamespace()
    function.name = "create_habit"
    function.arguments = '["item1", "item2"]'

    tc = SimpleNamespace(function=function)
    msg = SimpleNamespace(tool_calls=[tc])
    monkeypatch.setattr(
        llm._client.chat.completions, "create",
        _make_fake_create(msg),
    )
    result = llm.chat_with_tools(
        [{"role": "user", "content": "test"}],
        [{"type": "function", "function": {"name": "test"}}],
    )
    assert result["name"] == "create_habit"
    assert result["arguments"] == ["item1", "item2"]
