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


def test_real_sdk_and_graph_synthetic_suite_never_claim_online_acceptance(monkeypatch, tmp_path):
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
    budget.evidence = m.LocalEvidence({'LOCALAPPDATA': str(tmp_path), **ENV}, offline=True)
    result = m.run_suite(m.BASES[0], budget, offline=True)
    assert budget.evidence.status['raw'] == 'saved'
    assert all(s['final_answer'] == 'synthetic final' for s in budget.evidence.document['scenarios'])
    assert all(s['tool_results'] for s in budget.evidence.document['scenarios'])
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


@pytest.mark.parametrize('stage', m.STAGES)
def test_initialization_diagnostics_are_fixed_and_private(monkeypatch, capsys, stage):
    for key, value in {**ENV, 'SECRET_KEY': 'synthetic', 'DATABASE_URL': 'sqlite:///never-open.db'}.items():
        monkeypatch.setenv(key, value)
    versions = {'openai': '3.45.0', 'httpx': '0.28.1', 'httpcore': '1.0.9'}
    monkeypatch.setattr(m, 'version', lambda name: versions[name])

    def fail(base, budget):
        budget.stage = stage
        raise TypeError('private SQL URL ' + ENV['LLM_API_KEY'])

    monkeypatch.setattr(m, 'run_suite', fail)
    assert m.main(['--execute-paid-smoke', '--approved-profile', 'deepseek-official']) == 2
    output = capsys.readouterr().out
    result = json.loads(output)
    assert result['stage'] == stage
    assert result['error_code'] == 'smoke_' + stage + '_failed'
    assert result['exception_type'] == 'TypeError'
    assert result['requests'] == 0 and result['online_accepted'] is False
    assert 'private' not in output and ENV['LLM_API_KEY'] not in output


@pytest.mark.parametrize('point,stage', [('factory', 'application_init'), ('sdk', 'sdk_init')])
def test_real_initialization_path_records_failure_before_http(monkeypatch, point, stage):
    from app import factory
    from app.ai import llm_client
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(llm_client, '_llm', None)
    backend = Mock(side_effect=AssertionError('no send permitted'))
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', backend)
    failure = Mock(side_effect=RuntimeError('private initialization error'))
    if point == 'factory':
        monkeypatch.setattr(factory, 'create_app', failure)
    else:
        monkeypatch.setattr(m, 'sdk_client', failure)
    budget = m.Budget()
    with pytest.raises(RuntimeError):
        m.run_suite(m.BASES[0], budget, offline=True)
    assert budget.stage == stage and budget.total == 0
    backend.assert_not_called()


def test_agent_caught_pre_send_failure_keeps_diagnostic_stage(monkeypatch):
    from app.agent import service
    from app.ai import llm_client
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(llm_client, '_llm', None)
    budget = m.Budget()
    backend = Mock(side_effect=AssertionError('no send permitted'))
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', backend)

    def fail(question):
        budget.stage = 'http_validation'
        return {'ok': False, 'error_code': 'agent_execution_failed'}

    monkeypatch.setattr(service, 'answer', fail)
    result = m.run_suite(m.BASES[0], budget, offline=True)
    assert result['status'] == 'failed' and result['requests'] == 0
    assert result['scenarios'][0]['stage'] == 'http_validation'
    assert result['scenarios'][0]['error_code'] == 'smoke_http_validation_failed'
    assert result['online_accepted'] is False
    backend.assert_not_called()


@pytest.fixture
def local_evidence(tmp_path):
    evidence = m.LocalEvidence({'LOCALAPPDATA': str(tmp_path), 'LLM_API_KEY': 'never-export-key'}, offline=True)
    evidence.bind(1, [{'id': 10, 'name': '阅读', 'frequency': 'daily', 'note': ''}],
                  [(10, m.date.today())])
    evidence.start('S1', '有哪些习惯？')
    return evidence


def evidence_call(cid='one', name='list_user_habits', args=None):
    return SimpleNamespace(id=cid, function=SimpleNamespace(name=name, arguments=json.dumps(args or {})))


def test_evidence_default_disabled_and_missing_location(tmp_path):
    assert m.Budget().evidence is None
    assert not list(tmp_path.iterdir())
    with pytest.raises(m.SmokeError, match='evidence_location_invalid'):
        m.LocalEvidence({})


def test_evidence_saved_separately_and_review_pending(local_evidence):
    e = local_evidence
    e.finish(1, {'ok': True, 'answer': '<script>x</script> & "中文"'})
    status = e.save()
    assert status['raw'] == status['review'] == 'saved'
    raw = e.directory / (e.run_id + '.evidence.json')
    before = raw.read_bytes()
    payload = json.loads(before)
    assert payload['mode'] == 'offline_test'
    assert payload['scenarios'][0]['final_answer'] == '<script>x</script> & "中文"'
    review = e.directory / (e.run_id + '.review.json')
    data = json.loads(review.read_text())
    assert data['consistency_status'] == 'pending'
    assert data['scenarios'][0]['consistency_status'] == 'pending'
    review.write_text(json.dumps({'consistency_status': 'passed'}))
    assert raw.read_bytes() == before
    assert e.save()['raw'] == 'failed'  # Exclusive creation must not overwrite.
    assert raw.read_bytes() == before


def test_evidence_repeated_tools_match_call_ids(local_evidence):
    e = local_evidence
    e.calls([evidence_call('one'), evidence_call('two')])
    e.result(1, 'list_user_habits', {}, {'habits': []})
    value = {'habits': [{'id': 10, 'name': '阅读', 'frequency': 'daily', 'note': ''}]}
    e.result(1, 'list_user_habits', {}, value)
    value['habits'].clear()
    rows = e.current['tool_results']
    assert [r['tool_call_id'] for r in rows] == ['one', 'two']
    assert rows[0]['result'] == {'habits': []} and rows[1]['result']['habits'][0]['id'] == 10


def test_evidence_failed_tool_and_missing_answer(local_evidence):
    e = local_evidence
    e.calls([evidence_call()])
    e.result(1, 'list_user_habits', {}, failed=True)
    e.finish(1, {'ok': False, 'answer': 'error message'})
    assert e.current['tool_calls'][0]['execution_status'] == 'failed'
    assert e.current['tool_results'] == [] and e.current['final_answer'] is None
    assert e.current['answer_status'] == 'incomplete'


@pytest.mark.parametrize('value', [None, '', '  '])
def test_evidence_no_fabricated_final_answer(local_evidence, value):
    local_evidence.finish(1, {'ok': True, 'answer': value})
    assert local_evidence.current['answer_status'] == 'incomplete'


def test_evidence_rejects_foreign_user_and_rows(local_evidence):
    e = local_evidence
    e.calls([evidence_call()])
    with pytest.raises(m.SmokeError):
        e.finish(2, {'ok': True, 'answer': 'foreign'})
    with pytest.raises(m.SmokeError):
        e.result(1, 'list_user_habits', {}, {'habits': [
            {'id': 11, 'name': 'MVP15_B_ONLY', 'frequency': '', 'note': ''}]})
    assert not e.current['tool_results']


@pytest.mark.parametrize('text', ['never-export-key', 'MVP15_B_ONLY', 'Authorization: x',
                                  'sqlite:///private', 'Cookie: private'])
def test_evidence_sensitive_text_rejected(local_evidence, text):
    e = local_evidence
    e.finish(1, {'ok': True, 'answer': text})
    assert e.save()['error_code'] == 'evidence_sensitive_data'
    assert not e.directory.exists()


def test_evidence_size_limit_rejects_without_truncation(local_evidence, monkeypatch):
    monkeypatch.setattr(m, 'MAX_EVIDENCE_BYTES', 20)
    assert local_evidence.save()['error_code'] == 'evidence_size_limit'
    assert not local_evidence.directory.exists()


@pytest.mark.parametrize('review_failure', [False, True])
def test_evidence_write_failure_preserves_raw(local_evidence, monkeypatch, review_failure):
    from pathlib import Path
    e = local_evidence
    original = Path.open

    def deny(path, *args, **kwargs):
        if path.name.endswith('.review.json') if review_failure else path.name.endswith('.evidence.json'):
            raise OSError('private failure')
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', deny)
    status = e.save()
    assert status['error_code'] == 'evidence_write_failed'
    assert status['raw'] == ('saved' if review_failure else 'failed')
    assert status['review'] == ('failed' if review_failure else 'not_created')
    if review_failure:
        assert (e.directory / (e.run_id + '.evidence.json')).exists()


def test_evidence_actual_interval_uses_independent_dates(local_evidence):
    e = local_evidence
    today = m.date.today().isoformat()
    e.calls([evidence_call(name='get_checkin_summary', args={'days': 7})])
    e.result(1, 'get_checkin_summary', {'days': 7}, {'habits': [], 'start_date': today,
             'end_date': today, 'metric': 'checkin_count_not_completion_rate'})
    assert e.current['actual_date_ranges'][0]['independent_expected_counts'] == {'10': 1}


def test_evidence_flag_does_not_authorize_paid_execution(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    backend = Mock()
    monkeypatch.setattr(m, 'run_suite', backend)
    assert m.main(['--save-local-evidence']) == 2
    assert json.loads(capsys.readouterr().out)['error_code'] == 'explicit_confirmation_required'
    backend.assert_not_called()
    assert not list(tmp_path.iterdir())


def test_default_main_does_not_create_evidence(monkeypatch, tmp_path, capsys):
    for key, value in {**ENV, 'LOCALAPPDATA': str(tmp_path), 'SECRET_KEY': 'synthetic',
                       'DATABASE_URL': 'sqlite:///never-open.db'}.items():
        monkeypatch.setenv(key, value)
    versions = {'openai': '3.45.0', 'httpx': '0.28.1', 'httpcore': '1.0.9'}
    monkeypatch.setattr(m, 'version', lambda name: versions[name])
    factory = Mock(side_effect=AssertionError('must remain disabled'))
    monkeypatch.setattr(m, 'LocalEvidence', factory)
    monkeypatch.setattr(m, 'run_suite', lambda *args: {'status': 'content_review_required'})
    assert m.main(['--execute-paid-smoke', '--approved-profile', 'deepseek-official']) == 0
    assert 'evidence' not in json.loads(capsys.readouterr().out)
    factory.assert_not_called()
    assert not list(tmp_path.iterdir())


@pytest.fixture
def association_evidence(local_evidence, monkeypatch):
    import sqlalchemy as sa
    monkeypatch.setattr(sa.engine.Engine, 'connect', Mock(side_effect=AssertionError('database forbidden')))
    return local_evidence


def association_summary():
    today = m.date.today().isoformat()
    return {'habits': [], 'start_date': today, 'end_date': today,
            'metric': 'checkin_count_not_completion_rate'}


@pytest.mark.parametrize('different_names', [True, False])
def test_association_reverse_completion_is_rejected(association_evidence, different_names):
    e = association_evidence
    first = evidence_call('a') if different_names else evidence_call('a', 'get_checkin_summary', {'days': 7})
    second = evidence_call('b', 'get_checkin_summary', {'days': 30})
    e.calls([first, second])
    with pytest.raises(m.SmokeError, match='evidence_call_invalid'):
        e.result(1, 'get_checkin_summary', {'days': 30}, association_summary())
    assert e.current['tool_results'] == []
    assert all(c['execution_status'] == 'not_executed' for c in e.current['tool_calls'])


def test_association_identical_duplicates_use_serial_fifo(association_evidence):
    e = association_evidence
    e.calls([evidence_call('a'), evidence_call('b')])
    e.result(1, 'list_user_habits', {}, {'habits': []})
    e.result(1, 'list_user_habits', {}, {'habits': []})
    assert [r['tool_call_id'] for r in e.current['tool_results']] == ['a', 'b']
    assert all(c['execution_status'] == 'succeeded' for c in e.current['tool_calls'])


def test_association_threaded_reverse_completion_fails_closed(association_evidence):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    e = association_evidence
    e.calls([evidence_call('a'), evidence_call('b', 'get_checkin_summary', {'days': 7})])
    rejected = Event()

    def second():
        try:
            with pytest.raises(m.SmokeError, match='evidence_call_invalid'):
                e.result(1, 'get_checkin_summary', {'days': 7}, association_summary())
        finally:
            rejected.set()

    def first():
        assert rejected.wait(5)
        e.result(1, 'list_user_habits', {}, {'habits': []})

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(second), pool.submit(first)]
        for future in futures:
            future.result(timeout=10)
    assert [r['tool_call_id'] for r in e.current['tool_results']] == ['a']
    assert e.current['tool_calls'][1]['execution_status'] == 'not_executed'
    # This is rejection of unsupported order, NOT support for concurrent collectors.


def test_association_duplicate_id_is_rejected(association_evidence):
    e = association_evidence
    e.calls([evidence_call('same')])
    with pytest.raises(m.SmokeError, match='evidence_call_invalid'):
        e.calls([evidence_call('same')])
    assert len(e.current['tool_calls']) == 1 and e.current['tool_results'] == []


def test_association_unmatched_result_is_not_success(association_evidence):
    e = association_evidence
    e.calls([evidence_call()])
    with pytest.raises(m.SmokeError, match='evidence_call_invalid'):
        e.result(1, 'get_checkin_summary', {}, association_summary())
    assert e.current['tool_results'] == []


def test_association_failed_call_has_no_success_result(association_evidence):
    e = association_evidence
    e.calls([evidence_call()])
    e.result(1, 'list_user_habits', {}, failed=True)
    assert e.current['tool_calls'][0]['execution_status'] == 'failed'
    assert e.current['tool_results'] == []


@pytest.mark.parametrize('identical', [False, True])
def test_association_real_graph_runs_batch_serially(association_evidence, monkeypatch, identical):
    from threading import Lock, get_ident
    from time import sleep
    from app.agent import service
    from langsmith import tracing_context
    e = association_evidence
    calls = [evidence_call('a'), evidence_call('b')] if identical else [
        evidence_call('a'), evidence_call('b', 'get_checkin_summary', {'days': 7})]
    e.calls(calls)
    active = 0
    threads = []
    lock = Lock()

    def query(user_id, name, args):
        nonlocal active
        with lock:
            active += 1
            assert active == 1
            threads.append(get_ident())
        try:
            sleep(0.01)  # Make overlap observable if the actual graph is changed to fan out.
            value = {'habits': []} if name == 'list_user_habits' else association_summary()
            e.result(user_id, name, args, value)
            return value
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(service, 'query_tool', query)

    def model(messages, schemas):
        if messages[-1]['role'] == 'tool':
            return {'content': '合成回答', 'tool_calls': []}
        return {'content': '', 'tool_calls': [
            {'id': c.id, 'name': c.function.name, 'arguments': c.function.arguments} for c in calls]}

    with tracing_context(enabled=False):
        state = service.build_graph(1, model).invoke({
            'messages': [{'role': 'user', 'content': '合成问题'}], 'calls': 0, 'rounds': 0},
            config={'recursion_limit': 14})
    assert len(threads) == 2 and len(set(threads)) == 1
    assert [r['tool_call_id'] for r in e.current['tool_results']] == ['a', 'b']
    assert [msg['tool_call_id'] for msg in state['messages'] if msg['role'] == 'tool'] == ['a', 'b']
    assert state['calls'] == 2 and state['messages'][-1]['content'] == '合成回答'


def test_association_simultaneous_distinct_results_never_cross_ids(association_evidence):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    e = association_evidence
    e.calls([evidence_call('a'), evidence_call('b', 'get_checkin_summary', {'days': 7})])
    barrier = Barrier(2)

    def complete(name, args, value):
        barrier.wait(timeout=5)
        try:
            e.result(1, name, args, value)
            return 'recorded'
        except m.SmokeError:
            return 'rejected'

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(complete, 'list_user_habits', {}, {'habits': []}),
                   pool.submit(complete, 'get_checkin_summary', {'days': 7}, association_summary())]
        outcomes = [future.result(timeout=10) for future in futures]
    for row in e.current['tool_results']:
        assert row['tool_call_id'] == {'list_user_habits': 'a', 'get_checkin_summary': 'b'}[row['tool_name']]
    assert len(e.current['tool_results']) == outcomes.count('recorded')
    # An out-of-order attempt may be rejected. This does not certify thread safety
    # for identical duplicate calls, which are supported ONLY in the serial graph.
