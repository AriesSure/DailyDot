"""Real OpenAI SDK + public HTTPX2 MockTransport; zero network traffic."""
from contextlib import contextmanager
import json
from unittest.mock import Mock

import httpx2
import pytest
from openai import OpenAI

from app.ai import transport as t
from app.agent import service, conversations as c, dialogue
from tests.test_agent import logged as agent_logged

logged = agent_logged

KEY = 'synthetic-not-a-real-key'


def completion(calls=None, text='done'):
    return {'id': 'fake', 'object': 'chat.completion', 'created': 1, 'model': t.MODEL,
            'choices': [{'index': 0, 'finish_reason': 'tool_calls' if calls else 'stop',
                         'message': {'role': 'assistant', 'content': text, 'tool_calls': calls}}]}


@contextmanager
def sdk(handler, budget=None):
    budget = budget if budget is not None else t.Budget()
    guard = t.GuardedTransport(budget, KEY, backend=httpx2.MockTransport(handler))
    with httpx2.Client(transport=guard, follow_redirects=False, trust_env=False) as http:
        with OpenAI(api_key=KEY, base_url=t.BASE, http_client=http, max_retries=0) as client:
            yield client, budget


def send(client, **options):
    return client.with_options(**options).chat.completions.create(
        model=t.MODEL, messages=[{'role': 'user', 'content': 'synthetic'}])


@pytest.mark.parametrize('base', [t.BASE, t.BASE + '/'])
def test_approved_configuration(base):
    assert t.configuration({'LLM_API_KEY': KEY, 'LLM_BASE_URL': base, 'LLM_MODEL': t.MODEL}) == KEY


@pytest.mark.parametrize('field,value', [
    ('LLM_BASE_URL', 'http://api.deepseek.com'), ('LLM_BASE_URL', 'https://private.invalid'),
    ('LLM_BASE_URL', 'https://127.0.0.1'), ('LLM_BASE_URL', t.BASE + ':444'),
    ('LLM_BASE_URL', 'https://a:b@api.deepseek.com'), ('LLM_BASE_URL', t.BASE + '?x=y'),
    ('LLM_BASE_URL', t.BASE + '#fragment'), ('LLM_BASE_URL', t.BASE + '/v1'),
    ('LLM_BASE_URL', t.BASE + '/./'), ('LLM_MODEL', 'deepseek-chat'), ('LLM_API_KEY', ''),
])
def test_bad_configuration_has_no_client_or_send(monkeypatch, field, value):
    for name, item in {'LLM_API_KEY': KEY, 'LLM_BASE_URL': t.BASE, 'LLM_MODEL': t.MODEL}.items():
        monkeypatch.setenv(name, item)
    monkeypatch.setenv(field, value)
    constructor = Mock(side_effect=AssertionError('client must not exist'))
    monkeypatch.setattr(t, 'OpenAI', constructor)
    with pytest.raises(t.TransportError):
        with t.agent_client():
            pytest.fail('not admitted')
    constructor.assert_not_called()


def test_six_sends_then_seventh_blocked_and_separate_budgets():
    seen = []

    def backend(request):
        seen.append(request)
        return httpx2.Response(200, json=completion())

    with sdk(backend) as (client, budget):
        for i in range(6):
            assert send(client).choices[0].message.content == 'done'
            assert budget.sends == i + 1
        with pytest.raises(Exception) as exc:
            send(client)
        assert t.error_code(exc.value) == 'agent_http_budget_exhausted'
        assert len(seen) == budget.sends == 6
    with sdk(backend) as (client, budget):
        send(client)
        assert budget.sends == 1


@pytest.mark.parametrize('status', [301, 302, 303, 307, 308, 429, 500])
def test_redirects_and_statuses_never_retried(status):
    seen = []

    def backend(request):
        seen.append(request)
        return httpx2.Response(status, headers={'Location': 'https://private.invalid/steal'},
                               json={'error': {'message': 'private-not-exported'}})

    with sdk(backend) as (client, budget):
        with pytest.raises(Exception):
            send(client)
        assert len(seen) == budget.sends == 1
        assert seen[0].url.host == 'api.deepseek.com'


@pytest.mark.parametrize('failure', [httpx2.ConnectError, httpx2.ReadError, httpx2.ReadTimeout])
def test_failed_sends_count_without_retries(failure):
    def backend(request):
        raise failure('private ' + KEY, request=request)

    with sdk(backend) as (client, budget):
        model = service.ConfiguredModel(sdk_client=client, budget=budget)
        for _ in range(2):
            with pytest.raises(t.AgentError, match='agent_http_failed') as exc:
                model([], [])
            assert KEY not in str(exc.value) and 'private' not in str(exc.value)
        assert budget.sends == 2


def test_copy_retains_guard_and_cannot_change_target_model_or_timeouts():
    seen = []

    def backend(request):
        seen.append(request)
        assert max(request.extensions['timeout'].values()) <= 20
        return httpx2.Response(200, json=completion())

    with sdk(backend) as (client, budget):
        send(client, timeout=999, max_retries=0)
        with pytest.raises(Exception):
            send(client, base_url='https://private.invalid')
        with pytest.raises(Exception):
            client.chat.completions.create(model='other', messages=[])
        assert budget.sends == len(seen) == 1


def test_default_factory_ignores_proxy_and_environment_ca(monkeypatch):
    for name, value in {'LLM_API_KEY': KEY, 'LLM_MODEL': t.MODEL, 'LLM_BASE_URL': t.BASE,
                        'HTTPS_PROXY': 'http://127.0.0.1:1', 'ALL_PROXY': 'http://127.0.0.1:2',
                        'SSL_CERT_FILE': 'missing-env-ca.pem', 'SSL_CERT_DIR': 'missing-env-ca'}.items():
        monkeypatch.setenv(name, value)
    seen = []

    def handle(backend, request):
        seen.append(request.url.host)
        return httpx2.Response(200, json=completion())

    monkeypatch.setattr(httpx2.HTTPTransport, 'handle_request', handle)
    with t.agent_client() as (client, budget):
        send(client)
        assert budget.sends == 1
    assert seen == ['api.deepseek.com']


@pytest.mark.parametrize('advance,success', [(0, True), (89, True), (90, False)])
def test_soft_deadline_before_send(advance, success):
    now = [0]
    budget = t.Budget(clock=lambda: now[0])
    now[0] = advance
    seen = []

    def backend(request):
        seen.append(request)
        assert max(request.extensions['timeout'].values()) <= min(20, 90 - advance)
        return httpx2.Response(200, json=completion())

    with sdk(backend, budget) as (client, _):
        if success:
            send(client)
        else:
            with pytest.raises(Exception) as exc:
                send(client)
            assert t.error_code(exc.value) == 'agent_deadline_exceeded'
        assert len(seen) == budget.sends == int(success)


def test_deadline_after_response_and_no_later_send():
    now = [0]
    budget = t.Budget(clock=lambda: now[0])
    seen = []

    def backend(request):
        seen.append(request)
        now[0] = 91
        return httpx2.Response(200, json=completion())

    with sdk(backend, budget) as (client, _):
        for _ in range(2):
            with pytest.raises(Exception) as exc:
                send(client)
            assert t.error_code(exc.value) == 'agent_deadline_exceeded'
        assert len(seen) == budget.sends == 1


@pytest.mark.parametrize('headers,data', [
    ({}, b'x' * (t.MAX_RESPONSE_BYTES + 1)), ({'content-encoding': 'gzip'}, b'not-decoded')])
def test_response_limit(headers, data):
    with sdk(lambda request: httpx2.Response(
            200, headers=headers, stream=httpx2.ByteStream(data))) as (client, budget):
        with pytest.raises(Exception) as exc:
            send(client)
        assert t.error_code(exc.value) == 'agent_response_limit'
        assert budget.sends == 1


@pytest.mark.parametrize('history', [False, True])
def test_real_default_agent_path_shared_budget_and_resource_closure(logged, monkeypatch, history):
    monkeypatch.setenv('LLM_API_KEY', KEY)
    monkeypatch.setenv('LLM_BASE_URL', t.BASE)
    monkeypatch.setenv('LLM_MODEL', t.MODEL)
    requests, budgets, closes = [], [], []
    original_budget = t.Budget

    def budget_factory():
        item = original_budget()
        budgets.append(item)
        return item

    def handle(backend, request):
        requests.append(json.loads(request.content))
        if len(requests) % 2:
            calls = [{'id': 'one', 'type': 'function', 'function': {
                'name': 'list_user_habits', 'arguments': '{}'}}]
            return httpx2.Response(200, json=completion(calls, ''))
        return httpx2.Response(200, json=completion())

    monkeypatch.setattr(t, 'Budget', budget_factory)
    monkeypatch.setattr(httpx2.HTTPTransport, 'handle_request', handle)
    monkeypatch.setattr(httpx2.HTTPTransport, 'close', lambda backend: closes.append(backend))
    for _ in range(2):
        if history:
            from uuid import uuid4
            cid = c.create_conversation()['id']
            result = dialogue.send_message(cid, uuid4().hex, 'question')
        else:
            result = service.answer('question')
        assert result['ok'] and result['answer'] == 'done'
    assert len(budgets) == len(closes) == 2
    assert [b.sends for b in budgets] == [2, 2]
    assert len(requests) == 4 and requests[1]['messages'][-1]['role'] == 'tool'
    assert 'PRIVATE-B' not in json.dumps(requests)


def test_default_path_deadline_closes_turn_failed_and_never_retries(logged, monkeypatch):
    from uuid import uuid4
    for name, value in {'LLM_API_KEY': KEY, 'LLM_BASE_URL': t.BASE, 'LLM_MODEL': t.MODEL}.items():
        monkeypatch.setenv(name, value)
    now, requests = [0], []
    budget = t.Budget(clock=lambda: now[0])
    monkeypatch.setattr(t, 'Budget', lambda: budget)

    def backend(transport, request):
        requests.append(request)
        now[0] = 91
        return httpx2.Response(200, json=completion())

    monkeypatch.setattr(httpx2.HTTPTransport, 'handle_request', backend)
    cid, rid = c.create_conversation()['id'], uuid4().hex
    result = dialogue.send_message(cid, rid, 'question')
    assert not result['ok'] and result['error_code'] == 'agent_deadline_exceeded'
    assert c.get_request(cid, rid)['status'] == 'failed'
    assert c.get_conversation(cid)['active_request_id'] is None
    assert dialogue.send_message(cid, rid, 'question')['duplicate']
    assert budget.sends == len(requests) == 1


def test_default_answer_uses_one_budget_for_all_six_decisions(logged, monkeypatch):
    for name, value in {'LLM_API_KEY': KEY, 'LLM_BASE_URL': t.BASE, 'LLM_MODEL': t.MODEL}.items():
        monkeypatch.setenv(name, value)
    budget, requests = t.Budget(), []
    monkeypatch.setattr(t, 'Budget', lambda: budget)

    def backend(transport, request):
        requests.append(request)
        calls = ([{'id': str(len(requests)), 'type': 'function', 'function': {
            'name': 'list_user_habits', 'arguments': '{}'}}] if len(requests) < 6 else None)
        return httpx2.Response(200, json=completion(calls))

    monkeypatch.setattr(httpx2.HTTPTransport, 'handle_request', backend)
    assert service.answer('question')['ok']
    assert budget.sends == len(requests) == 6


def test_default_sdk_failure_logs_and_result_are_safe(logged, monkeypatch, caplog):
    import logging
    for name, value in {'LLM_API_KEY': KEY, 'LLM_BASE_URL': t.BASE, 'LLM_MODEL': t.MODEL}.items():
        monkeypatch.setenv(name, value)
    count = []

    def backend(transport, request):
        count.append(1)
        return httpx2.Response(500, json={'error': {'message': KEY + ' private body'}})

    monkeypatch.setattr(httpx2.HTTPTransport, 'handle_request', backend)
    with caplog.at_level(logging.DEBUG):
        result = service.answer('question')
    assert not result['ok'] and result['error_code'] == 'agent_http_failed'
    assert len(count) == 1
    assert KEY not in json.dumps(result) + caplog.text
    assert 'private body' not in json.dumps(result) + caplog.text
