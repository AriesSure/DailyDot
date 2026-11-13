"""Offline transport/SDK/graph checks. No actual provider request is permitted."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from tools import manual_agent_online_smoke as m
from app.agent.service import ConfiguredModel
from app.agent.tools import AgentError


ENV = {'LLM_API_KEY': 'synthetic-never-real', 'LLM_BASE_URL': m.BASES[0], 'LLM_MODEL': m.MODEL}


@pytest.mark.parametrize('args,code', [
    ([], 'explicit_confirmation_required'),
    (['--execute-paid-smoke'], 'target_approval_required'),
    (['--execute-paid-smoke', '--approved-profile', 'deepseek-official'], 'key_missing')])
def test_cli_refuses_without_authorization_or_key(monkeypatch, capsys, args, code):
    monkeypatch.delenv('LLM_API_KEY', raising=False)
    backend = Mock(side_effect=AssertionError('must not run suite'))
    monkeypatch.setattr(m, 'run_suite', backend)
    assert m.main(args) == 2
    assert json.loads(capsys.readouterr().out)['error_code'] == code
    backend.assert_not_called()


@pytest.mark.parametrize('key,value', [
    ('LLM_BASE_URL', 'http://api.deepseek.com'),
    ('LLM_BASE_URL', 'https://unknown.invalid'), ('LLM_BASE_URL', m.BASES[0]+'?key=secret'),
    ('LLM_MODEL', 'unapproved-model'), ('LLM_API_KEY', ''),
    ('LLM_MODEL', 'deepseek-chat'), ('LLM_BASE_URL', m.BASES[0]+'/v1')])
def test_unapproved_config_is_rejected(key, value):
    with pytest.raises(m.SmokeError):
        m.configuration({**ENV, key: value})


def test_shared_budget_cannot_reset_or_exceed_limits():
    budget = m.Budget()
    for scene in ('S1', 'S2', 'S3'):
        budget.start(scene)
        for _ in range(4):
            budget.charge()
        with pytest.raises(m.SmokeError):
            budget.charge()
    assert budget.total == 12
    with pytest.raises(m.SmokeError):
        budget.start('S1')
    assert budget.total == 12


def request(url=m.BASES[0]+'/chat/completions', **extra):
    return httpx.Request('POST', url, json={
        'model': m.MODEL, 'messages': [{'role': 'user', 'content': 'synthetic'}], **extra})


def test_transport_limit_is_before_backend_and_failures_count(monkeypatch):
    budget = m.Budget()
    budget.start('S1')
    backend = Mock(side_effect=httpx.ConnectError('private details'))
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', backend)
    with m.GuardedTransport(budget, []) as transport:
        for _ in range(4):
            with pytest.raises(httpx.ConnectError):
                transport.handle_request(request())
        with pytest.raises(m.SmokeError):
            transport.handle_request(request())
    assert budget.total == backend.call_count == 4


@pytest.mark.parametrize('url', [
    'http://api.deepseek.com/chat/completions',
    'https://other.invalid/chat/completions', m.BASES[0]+'/other',
    'https://user:password@api.deepseek.com/chat/completions'])
def test_transport_rejects_target_before_backend(monkeypatch, url):
    backend = Mock()
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', backend)
    budget = m.Budget()
    budget.start('S1')
    with m.GuardedTransport(budget, []) as transport, pytest.raises(m.SmokeError):
        transport.handle_request(request(url))
    backend.assert_not_called()
    assert budget.total == 0


def test_actual_sdk_redirect_and_server_error_never_retry(monkeypatch):
    for status in (302, 500):
        budget = m.Budget()
        budget.start('S1')
        backend = Mock(return_value=httpx.Response(status, headers={'Location': 'https://other.invalid'},
                                                   json={'error': {'message': 'synthetic'}}))
        monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', backend)
        with m.sdk_client(ENV['LLM_API_KEY'], m.BASES[0], budget, []) as sdk:
            with pytest.raises(Exception):
                sdk.chat.completions.create(model=m.MODEL, messages=[{'role': 'user', 'content': 'x'}])
        assert budget.total == backend.call_count == 1


@pytest.mark.parametrize('reason,content,calls,code', [
    ('length', 'partial', [], 'agent_response_truncated'),
    ('unknown', 'text', [], 'agent_response_invalid'), ('stop', '', [], 'agent_response_invalid'),
    ('tool_calls', '', [], 'agent_response_invalid')])
def test_finish_reason_is_not_assumed_success(monkeypatch, reason, content, calls, code):
    from app.ai import llm_client
    sdk = Mock()
    sdk.with_options.return_value.chat.completions.create.return_value = SimpleNamespace(choices=[
        SimpleNamespace(finish_reason=reason, message=SimpleNamespace(content=content, tool_calls=calls))])
    monkeypatch.setattr(llm_client, 'get_llm', lambda: SimpleNamespace(
        available=True, model=m.MODEL, _get_client=lambda: sdk))
    with pytest.raises(AgentError, match=code):
        ConfiguredModel()([], [])


def test_foreign_tool_rows_are_blocked_before_http(monkeypatch):
    budget = m.Budget()
    budget.start('S1')
    backend = Mock()
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', backend)
    messages = [{'role': 'tool', 'content': json.dumps({'habits': [{'id': 999}]})}]
    with m.GuardedTransport(budget, [1]) as transport, pytest.raises(m.SmokeError):
        transport.handle_request(request(messages=messages))
    assert budget.total == 0
    backend.assert_not_called()


def test_real_sdk_and_graph_synthetic_suite_never_claim_online_acceptance(monkeypatch):
    from app.ai import llm_client
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(llm_client, '_llm', None)
    budget = m.Budget()
    histories = []

    def backend(transport, req):
        body = json.loads(req.content)
        histories.append(body)
        scene = budget.scene
        if budget.counts[scene] == 1:
            names = {'S1': ['list_user_habits'], 'S2': ['get_checkin_summary'],
                     'S3': ['list_user_habits', 'get_checkin_summary']}[scene]
            message = {'role': 'assistant', 'content': None, 'tool_calls': [
                {'id': str(i), 'type': 'function', 'function': {'name': name, 'arguments': '{}'}}
                for i, name in enumerate(names)]}
            reason = 'tool_calls'
        else:
            message, reason = {'role': 'assistant', 'content': 'synthetic final'}, 'stop'
        return httpx.Response(200, json={
            'id': 'synthetic', 'object': 'chat.completion',
            'created': 1, 'model': m.MODEL, 'choices': [{'index': 0, 'finish_reason': reason, 'message': message}]})
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', backend)
    result = m.run_suite(m.BASES[0], budget, offline=True)
    assert result['mode'] == 'offline_test' and result['online_accepted'] is False
    assert result['requests'] == len(histories) == 6
    assert all(s['finish_reasons'] == ['tool_calls', 'stop'] for s in result['scenarios'])
    assert len(result['scenarios']) == 3 and all(s['required_tools_used'] for s in result['scenarios'])
    assert all(s['answer_consistency'] == 'unverified' for s in result['scenarios'])
    outputs = [json.loads(message['content']) for body in histories
               for message in body['messages'] if message['role'] == 'tool']
    lists = [data for data in outputs if 'metric' not in data]
    summaries = [data for data in outputs if 'metric' in data]
    assert lists and summaries
    assert all([h['frequency'] for h in data['habits']] == [
        'Every day this week', 'Once a week', 'Every day this week'] for data in lists)
    assert all([h['checkins'] for h in data['habits']] == [3, 1, 2] for data in summaries)
    assert 'MVP15_B_ONLY' not in json.dumps(histories)
    encoded = json.dumps(result)
    assert ENV['LLM_API_KEY'] not in encoded and 'Authorization' not in encoded


def test_main_failure_does_not_lose_charged_requests_or_export_exception(monkeypatch, capsys):
    for key, value in {**ENV, 'SECRET_KEY': 'synthetic', 'DATABASE_URL': 'sqlite:///never-open.db'}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(m, 'version', lambda name: {'openai': '3.45.0', 'httpx': '0.28.1', 'httpcore': '1.0.9'}[name])

    def fail(base, budget):
        budget.start('S1')
        budget.charge()
        raise RuntimeError('private ' + ENV['LLM_API_KEY'])

    monkeypatch.setattr(m, 'run_suite', fail)
    assert m.main(['--execute-paid-smoke', '--approved-profile', 'deepseek-official']) == 2
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload['status'] == 'failed' and payload['requests'] == 1
    assert ENV['LLM_API_KEY'] not in output and 'private' not in output
