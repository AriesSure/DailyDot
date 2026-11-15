"""Real graph and isolated SQLite, with no live model requests."""
from uuid import uuid4
import json

import pytest
from flask_login import login_user

from app.agent import conversations as c, dialogue, service
from app.agent.tools import AgentError
from app.models import AgentAdmissionGate, User, Habit
from tests.test_agent import call, final, scripted


@pytest.fixture
def logged(app, db):
    a, b = User(username='dialogue-a', email='da@example.invalid'), User(
        username='dialogue-b', email='db@example.invalid')
    db.session.add(AgentAdmissionGate(gate_key='global', lock_version=0))
    db.session.add_all([a, b])
    db.session.flush()
    db.session.add_all([Habit(user_id=a.id, habit_name='READ-A', icon='book'),
                        Habit(user_id=b.id, habit_name='PRIVATE-B', icon='book')])
    db.session.commit()
    with app.test_request_context():
        login_user(a)
        yield db, a, b


def test_two_turns_preserve_protocol_and_reset_counters(logged):
    cid = c.create_conversation()['id']
    seen = []
    for _ in range(2):
        result = dialogue.send_message(cid, uuid4().hex, '最近的情况', model=scripted([
            call('list_user_habits'), call('get_checkin_summary', cid='two'), final()], seen))
        assert result['ok']
    stored = c.history(cid, completed_only=True)
    assert len(stored) == 2
    assert [m['role'] for m in stored[0]['messages']] == [
        'user', 'assistant', 'tool', 'assistant', 'tool', 'assistant']
    assert stored[0]['messages'][1]['tool_calls'][0]['id'] == 'one'
    assert stored[0]['messages'][2]['tool_name'] == 'list_user_habits'
    assert len(seen) == 6 and sum(m['role'] == 'system' for m in seen[3]) == 1
    assert seen[3][-1] == {'role': 'user', 'content': '最近的情况'}
    historical = seen[3][1:-1]
    assert historical[1]['tool_calls'][0]['id'].startswith('hist_')
    assert historical[2]['tool_call_id'] == historical[1]['tool_calls'][0]['id']
    assert 'PRIVATE-B' not in json.dumps(seen)


def test_duplicate_no_model_and_busy(logged):
    cid, rid = c.create_conversation()['id'], uuid4().hex
    c.begin_turn(cid, rid, 'question')

    def model(*args):
        pytest.fail('duplicate/busy must not execute model')
    assert dialogue.send_message(cid, rid, 'question', model=model)['duplicate']
    assert dialogue.send_message(cid, uuid4().hex, 'other', model=model)['error_code'] == 'agent_user_busy'
    assert dialogue.send_message(cid, rid, 'changed', model=model)['error_code'] == 'chat_request_conflict'


def test_completed_duplicate_no_reexecution(logged):
    cid, rid = c.create_conversation()['id'], uuid4().hex
    assert dialogue.send_message(cid, rid, 'hello', model=scripted([final()], []))['ok']
    result = dialogue.send_message(cid, rid, 'hello', model=lambda *args: pytest.fail('not retried'))
    assert result['ok'] and result['duplicate']
    assert len(c.history(cid)) == 1


def test_isolation_and_failed_turns_not_replayed(logged):
    _, a, b = logged
    cid = c.create_conversation()['id']
    assert not dialogue.send_message(cid, uuid4().hex, 'failed secret',
                                     model=lambda *args: (_ for _ in ()).throw(RuntimeError('private')))['ok']
    seen = []
    assert dialogue.send_message(cid, uuid4().hex, 'new', model=scripted([final()], seen))['ok']
    assert 'failed secret' not in json.dumps(seen)
    other = c.create_conversation()['id']
    seen.clear()
    assert dialogue.send_message(other, uuid4().hex, 'isolated', model=scripted([final()], seen))['ok']
    assert len(seen[0]) == 2
    login_user(b)
    assert dialogue.send_message(cid, uuid4().hex, 'foreign', model=lambda *args: pytest.fail())[
        'error_code'] == 'chat_not_found'
    login_user(a)


def test_tool_failure_preserves_actual_prefix(logged, monkeypatch):
    cid = c.create_conversation()['id']
    monkeypatch.setattr(service, 'query_tool', lambda *args: (_ for _ in ()).throw(AgentError('private')))
    result = dialogue.send_message(cid, uuid4().hex, 'query', model=scripted([call('list_user_habits')], []))
    assert not result['ok']
    row = c.history(cid)[0]
    assert row['status'] == 'failed' and [m['role'] for m in row['messages']] == ['user', 'assistant']
    assert c.get_conversation(cid)['active_request_id'] is None


@pytest.mark.parametrize('failure', ['trace', 'budget', 'persistence', 'late'])
def test_finalization_failure_no_false_success(logged, monkeypatch, failure):
    cid, rid = c.create_conversation()['id'], uuid4().hex
    original = c.finish_turn
    count = []

    def finish(*args, **kwargs):
        count.append(kwargs['status'])
        if len(count) == 1:
            code = {'trace': 'chat_protocol_invalid', 'budget': 'chat_protocol_invalid',
                    'persistence': 'chat_persistence_failed', 'late': 'chat_ownership_lost'}[failure]
            raise c.ConversationError(code)
        return original(*args, **kwargs)

    monkeypatch.setattr(c, 'finish_turn', finish)
    seen = []
    result = dialogue.send_message(cid, rid, 'question', model=scripted([final()], seen))
    assert not result['ok'] and len(seen) == 1
    if failure in ('trace', 'budget'):
        assert c.get_request(cid, rid)['status'] == 'failed' and count == ['completed', 'failed']
    else:
        assert c.get_request(cid, rid)['status'] == 'pending' and count == ['completed']
        assert result['recovery'] == 'state_unresolved'


def test_real_collector_overflow_fails_without_pending_leak(logged, monkeypatch):
    cid, rid = c.create_conversation()['id'], uuid4().hex
    monkeypatch.setattr(service, 'query_tool', lambda *args: {'data': 'x' * 15800})
    batch = call('list_user_habits')
    for index in range(1, 5):
        batch['tool_calls'] += call('list_user_habits', cid=str(index))['tool_calls']
    result = dialogue.send_message(cid, rid, 'question', model=scripted([batch], []))
    assert not result['ok']
    assert c.get_request(cid, rid)['status'] == 'failed'
    assert c.get_conversation(cid)['active_request_id'] is None
    row = c.history(cid)[0]
    c.check_message_budget(row['messages'])
    assert len([m for m in row['messages'] if m['role'] == 'tool']) < 5


def test_truncated_context_current_question_and_time_rules(logged, monkeypatch):
    from datetime import datetime, timezone, timedelta
    from app.agent import admission
    tick = [datetime.now(timezone.utc) + timedelta(seconds=1)]

    def database_time(session):
        tick[0] += timedelta(seconds=61)
        return tick[0]

    monkeypatch.setattr(admission, 'database_utc', database_time)
    cid = c.create_conversation()['id']
    for i in range(8):
        assert dialogue.send_message(cid, uuid4().hex, f'old-{i}', model=scripted([final()], []))['ok']
    seen = []
    result = dialogue.send_message(cid, uuid4().hex, '那比上周呢', model=scripted([final('需要查询或澄清')], seen))
    assert result['ok'] and result['history_truncated']
    assert seen[0][-1]['content'] == '那比上周呢'
    assert 'old-0' not in json.dumps(seen) and 'old-7' in json.dumps(seen)
    assert '澄清' in seen[0][0]['content'] and '不是当前实时事实' in seen[0][0]['content']


def test_internal_identity_rechecked(logged):
    result = service.run_agent_with_history('q', user_id=logged[2].id, turns=[], model=lambda *args: pytest.fail())
    assert not result['ok'] and result['error_code'] == 'agent_auth_required'


def test_actual_late_executor_cannot_release_new_owner(logged):
    cid, old, new = c.create_conversation()['id'], uuid4().hex, uuid4().hex

    def late_model(*args):
        c.finish_turn(cid, old, status='interrupted', messages=[])
        c.begin_turn(cid, new, 'new owner')
        return final('late')

    result = dialogue.send_message(cid, old, 'old owner', model=late_model)
    assert not result['ok'] and result['error_code'] == 'chat_ownership_lost'
    assert c.get_request(cid, old)['status'] == 'interrupted'
    assert c.get_conversation(cid)['active_request_id'] == new


def test_invalid_returned_trace_closes_failed(logged, monkeypatch):
    cid, rid = c.create_conversation()['id'], uuid4().hex
    monkeypatch.setattr(dialogue, 'run_agent_with_history', lambda *args, **kwargs: {
        'ok': True, 'answer': 'bad', 'error_code': None, 'history_truncated': False,
        'messages': [{'role': 'user', 'content': 'question'}, {'role': 'system', 'content': 'bad'}]})
    result = dialogue.send_message(cid, rid, 'question')
    assert not result['ok'] and result['recovery'] == 'failed_saved'
    assert c.get_request(cid, rid)['status'] == 'failed'
    assert len(c.history(cid)[0]['messages']) == 1


def test_partial_batch_records_only_executed_results(logged, monkeypatch):
    cid = c.create_conversation()['id']
    executed = []

    def query(*args):
        executed.append(args[1])
        if len(executed) == 2:
            raise RuntimeError('private')
        return {'habits': []}

    monkeypatch.setattr(service, 'query_tool', query)
    batch = call('list_user_habits')
    batch['tool_calls'] += call('get_checkin_summary', cid='two')['tool_calls']
    assert not dialogue.send_message(cid, uuid4().hex, 'query', model=scripted([batch], []))['ok']
    messages = c.history(cid)[0]['messages']
    assert [m['role'] for m in messages] == ['user', 'assistant', 'tool']
    assert messages[-1]['tool_call_id'] == 'one'


def test_counters_are_fresh_each_turn_and_current_duplicates_rejected(logged):
    cid = c.create_conversation()['id']
    for _ in range(2):
        result = dialogue.send_message(cid, uuid4().hex, 'q', model=scripted([
            call('list_user_habits'), call('list_user_habits', cid='two'),
            call('list_user_habits', cid='three'), final()], []))
        assert result['ok']
    result = dialogue.send_message(cid, uuid4().hex, 'bad', model=scripted([
        call('list_user_habits'), call('list_user_habits')], []))
    assert not result['ok'] and result['error_code'] == 'agent_response_invalid'


def test_mapped_history_id_still_protected_from_current_collision(logged):
    cid = c.create_conversation()['id']
    assert dialogue.send_message(cid, uuid4().hex, 'q', model=scripted([
        call('list_user_habits'), final()], []))['ok']
    tid = c.history(cid)[0]['id']
    result = dialogue.send_message(cid, uuid4().hex, 'q', model=scripted([
        call('list_user_habits', cid=f'hist_{tid:x}_0')], []))
    assert not result['ok'] and result['error_code'] == 'agent_response_invalid'


@pytest.mark.parametrize('committed', [False, True])
def test_begin_persistence_failure_is_unresolved_without_compensation(logged, monkeypatch, committed):
    from unittest.mock import Mock
    cid, rid = c.create_conversation()['id'], uuid4().hex
    original = c.begin_admitted_turn

    def uncertain_begin(actual_cid, actual_rid, question):
        if committed:
            original(actual_cid, actual_rid, question)
        raise c.ConversationError('chat_persistence_failed')

    begin = Mock(side_effect=uncertain_begin)
    finish, model = Mock(), Mock()
    monkeypatch.setattr(c, 'begin_admitted_turn', begin)
    monkeypatch.setattr(c, 'finish_turn', finish)
    result = dialogue.send_message(cid, rid, 'question', model=model)
    assert result['ok'] is False
    assert result['error_code'] == 'chat_persistence_failed'
    assert result['recovery'] == 'state_unresolved'
    begin.assert_called_once_with(cid, rid, 'question')
    finish.assert_not_called()
    model.assert_not_called()
    # Same-ID reads are safe whether the commit occurred or not. In the former
    # case, the service must not release the real pending request's ownership.
    row = c.get_request(cid, rid)
    if committed:
        assert row['status'] == 'pending' and row['client_request_id'] == rid
        assert c.get_conversation(cid)['active_request_id'] == rid
    else:
        assert row is None and c.get_conversation(cid)['active_request_id'] is None


@pytest.mark.parametrize('question', [None, '', 'x' * 2001])
def test_pre_begin_validation_failure_needs_no_recovery(logged, monkeypatch, question):
    from unittest.mock import Mock
    cid, rid = c.create_conversation()['id'], uuid4().hex
    begin, finish, model = Mock(), Mock(), Mock()
    monkeypatch.setattr(c, 'begin_admitted_turn', begin)
    monkeypatch.setattr(c, 'finish_turn', finish)
    result = dialogue.send_message(cid, rid, question, model=model)
    assert result['ok'] is False and result['error_code'] == 'chat_content_invalid'
    assert result['recovery'] == 'not_needed'
    begin.assert_not_called()
    finish.assert_not_called()
    model.assert_not_called()
