"""Real HTTP/CSRF, database services and StateGraph; fake model only."""
from contextlib import contextmanager
from datetime import timezone
import json
import re
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

from flask_login import login_user
import pytest
import sqlalchemy as sa

from app.agent import conversations as c, service
from app.extensions import db
from app.models import AgentAdmissionGate, User, Habit, Conversation, ChatTurn, ChatMessage
from tests.test_agent import call, final


@pytest.fixture
def api(app, monkeypatch):
    app.config['WTF_CSRF_ENABLED'] = True
    users = [User(username='api-' + str(i), email=str(i) + '@example.invalid') for i in range(3)]
    db.session.add_all(users + [AgentAdmissionGate(gate_key='global', lock_version=0)])
    db.session.flush()
    db.session.add_all([Habit(user_id=users[0].id, habit_name='READ-A', icon='book'),
                        Habit(user_id=users[1].id, habit_name='PRIVATE-B', icon='book')])
    db.session.commit()
    state = SimpleNamespace(replies=[final('<b>synthetic answer</b>')], calls=[])

    @contextmanager
    def model_context(model):
        replies = iter(state.replies)

        def fake(messages, tools):
            state.calls.append(messages)
            value = next(replies)
            if isinstance(value, Exception):
                raise value
            return value
        yield fake
    monkeypatch.setattr(service, 'execution_model', model_context)

    def browser(user=None):
        client = app.test_client()
        if user is not None:
            with client.session_transaction() as session:
                session['_user_id'] = str(user.id)
                session['_fresh'] = True
        with app.app_context():
            page = client.get('/auth/account' if user else '/auth/login')
        token = re.search(r'<meta name="csrf-token" content="([^"]+)"', page.get_data(as_text=True)).group(1)

        def send(method, path, *, csrf=True, **kwargs):
            headers = kwargs.pop('headers', {})
            if csrf:
                headers.setdefault('X-CSRFToken', token)
            with app.app_context():
                response = client.open(path, method=method, headers=headers, **kwargs)
            if path.startswith('/agent'):
                assert response.headers['Cache-Control'] == 'no-store'
                assert set(response.get_json()) == {'success', 'data', 'error'}
            return response
        return send
    return SimpleNamespace(app=app, users=users, browser=browser, send=browser(users[0]), state=state)


def create(api):
    response = api.send('POST', '/agent/conversations', json={})
    assert response.status_code == 201
    row = response.json['data']
    assert set(row) == {'conversation_id', 'title', 'created_at', 'updated_at'}
    assert row['title'] == '新对话' and row['created_at'].endswith('Z')
    return row['conversation_id']


def send_message(api, cid, rid=None, question='question'):
    return api.send('POST', f'/agent/conversations/{cid}/messages',
                    json={'request_id': rid or uuid4().hex, 'question': question})


def seed_turn(api, cid, rid, status='pending'):
    with api.app.test_request_context():
        login_user(api.users[0])
        c.begin_turn(cid, rid, 'question')
        if status != 'pending':
            c.finish_turn(cid, rid, status=status, messages=[{'role': 'assistant', 'content': 'persisted'}]
                          if status == 'completed' else [])


def snapshot():
    db.session.expire_all()
    return {model.__tablename__: tuple(db.session.execute(sa.select(model.__table__)).all())
            for model in (AgentAdmissionGate, Conversation, ChatTurn, ChatMessage)}


def test_full_flow_tools_history_idempotency_and_read_only(api):
    cid, rid = create(api), uuid4().hex
    api.state.replies = [call('list_user_habits'), final()]
    response = send_message(api, cid, rid)
    assert response.status_code == 200 and response.json['data']['status'] == 'completed'
    assert response.json['data']['answer'] == '完成查询'
    assert len(api.state.calls) == 2
    assert 'READ-A' in json.dumps(api.state.calls, ensure_ascii=False)
    assert 'PRIVATE-B' not in json.dumps(api.state.calls, ensure_ascii=False)
    before_duplicate, calls = snapshot(), len(api.state.calls)
    duplicate = send_message(api, cid, rid)
    assert duplicate.status_code == 200 and duplicate.json['data']['answer'] == '完成查询'
    assert duplicate.json['data']['history_truncated'] is None
    before = snapshot()
    assert {k: v for k, v in before.items() if k != 'agent_admission_gate'} == {
        k: v for k, v in before_duplicate.items() if k != 'agent_admission_gate'}
    assert before['agent_admission_gate'][0][1] == before_duplicate['agent_admission_gate'][0][1] + 1
    history = api.send('GET', f'/agent/conversations/{cid}/history').json['data']['turns']
    assert [m['role'] for m in history[0]['messages']] == ['user', 'assistant', 'tool', 'assistant']
    assert history[0]['messages'][1]['tool_calls'][0]['id'] == history[0]['messages'][2]['tool_call_id']
    displayed = json.loads(history[0]['messages'][2]['content'])
    assert set(displayed['habits'][0]) == {'name', 'frequency', 'note'}
    assert displayed['habits'][0]['name'] == 'READ-A'
    assert api.send('GET', '/agent/conversations').status_code == 200
    result = api.send('GET', f'/agent/conversations/{cid}/requests/{rid}')
    assert result.json['data']['answer'] == '完成查询'
    assert snapshot() == before and len(api.state.calls) == calls
    forbidden = {'request_digest', 'active_request_id', 'active_started_at', 'id', 'turn_id', 'conversation_id'}
    assert not (set(history[0]) & forbidden)
    for message in history[0]['messages']:
        assert not (set(message) & forbidden)


@pytest.mark.parametrize('status', ['pending', 'completed', 'failed', 'interrupted'])
def test_existing_statuses_no_execution_or_release(api, status):
    cid, rid = create(api), uuid4().hex
    seed_turn(api, cid, rid, status)
    before = snapshot()
    get = api.send('GET', f'/agent/conversations/{cid}/requests/{rid}')
    assert get.status_code == 200 and get.json['data']['status'] == status
    assert snapshot() == before and api.state.calls == []
    post = send_message(api, cid, rid)
    assert post.status_code == (202 if status == 'pending' else 200)
    assert post.json['data']['status'] == status
    after = snapshot()
    assert {k: v for k, v in after.items() if k != 'agent_admission_gate'} == {
        k: v for k, v in before.items() if k != 'agent_admission_gate'}
    assert after['agent_admission_gate'][0][1] == before['agent_admission_gate'][0][1] + 1
    assert api.state.calls == []
    assert get.json['data']['history_truncated'] is None


@pytest.mark.parametrize('value', [{}, [], None, {'user_id': 1}, {'request_id': 'bad', 'question': 'ok'},
                                   {'request_id': 'A' * 32, 'question': 'ok'},
                                   {'request_id': 'a' * 32, 'question': ''},
                                   {'request_id': 'a' * 32, 'question': ' '},
                                   {'request_id': 'a' * 32, 'question': 'x' * 2001},
                                   {'request_id': 'a' * 32, 'question': 1}])
def test_invalid_message_body_rejected_before_agent(api, value):
    cid = create(api)
    response = api.send('POST', f'/agent/conversations/{cid}/messages',
                        data=json.dumps(value), content_type='application/json')
    assert response.status_code == 400 and api.state.calls == []
    assert db.session.scalar(sa.select(sa.func.count()).select_from(ChatTurn)) == 0


@pytest.mark.parametrize('field', ['user_id', 'messages', 'system', 'system_prompt', 'model', 'base_url',
                                   'api_key', 'tools', 'temperature', 'max_tokens'])
def test_client_overrides_rejected(api, field):
    cid = create(api)
    response = api.send('POST', f'/agent/conversations/{cid}/messages',
                        json={'request_id': uuid4().hex, 'question': 'ok', field: 'forbidden'})
    assert response.status_code == 400 and not api.state.calls
    assert api.send('POST', '/agent/conversations', json={field: 'forbidden'}).status_code == 400


@pytest.mark.parametrize('raw,ctype,status', [('not json', 'text/plain', 415),
                                              ('{', 'application/json', 400),
                                              ('', 'application/json', 400),
                                              ('x' * 16385, 'application/json', 413),
                                              ('{"a":1,"a":2}', 'application/json', 400)])
def test_json_and_body_limit(api, raw, ctype, status):
    cid = create(api)
    assert api.send('POST', f'/agent/conversations/{cid}/messages', data=raw,
                    content_type=ctype).status_code == status
    assert not api.state.calls


@pytest.mark.parametrize('query', ['offset=-1', 'limit=0', 'limit=101', 'offset=10001', 'limit=true',
                                   'limit=1.5', 'limit=2&limit=3', 'user_id=2', 'offset=99999999999'])
def test_strict_pagination(api, query):
    cid = create(api)
    assert api.send('GET', '/agent/conversations?' + query).status_code == 400
    assert api.send('GET', f'/agent/conversations/{cid}/history?' + query).status_code == 400


def test_list_pagination_and_history_whole_turns(api):
    first, second = create(api), create(api)
    rows = api.send('GET', '/agent/conversations?offset=0&limit=1').json['data']['conversations']
    assert [r['conversation_id'] for r in rows] == [second]
    assert api.send('GET', '/agent/conversations?offset=1&limit=1').json['data']['conversations'][0][
        'conversation_id'] == first
    for status in ('completed', 'failed'):
        seed_turn(api, first, uuid4().hex, status)
    response = api.send('GET', f'/agent/conversations/{first}/history?offset=1&limit=1')
    assert [t['turn_no'] for t in response.json['data']['turns']] == [2]
    assert api.send('GET', f'/agent/conversations/{first}/history?limit=21').status_code == 400


@pytest.mark.parametrize('method,path', [('GET', '/agent/conversations'), ('POST', '/agent/conversations'),
                                         ('GET', '/agent/conversations/' + 'a' * 32 + '/history'),
                                         ('POST', '/agent/conversations/' + 'a' * 32 + '/messages'),
                                         ('GET', '/agent/conversations/' + 'a' * 32 + '/requests/' + 'b' * 32)])
def test_unauthenticated_json_no_redirect(api, method, path):
    assert api.browser()(method, path, json={}, csrf=False).status_code == 401


@pytest.mark.parametrize('token', [None, 'wrong'])
def test_real_csrf_failure(api, token):
    response = api.send('POST', '/agent/conversations', csrf=False,
                        headers={'X-CSRFToken': token} if token else {}, json={})
    assert response.status_code == 400 and response.json['error']['code'] == 'csrf_failed'
    assert not api.state.calls


def test_user_isolation_and_unknown_paths(api):
    cid, rid = create(api), uuid4().hex
    seed_turn(api, cid, rid, 'completed')
    other = api.browser(api.users[1])
    for path in (f'/agent/conversations/{cid}/history', f'/agent/conversations/{cid}/requests/{rid}'):
        assert other('GET', path).status_code == 404
    assert other('POST', f'/agent/conversations/{cid}/messages',
                 json={'request_id': rid, 'question': 'question'}).status_code == 404
    assert other('GET', '/agent/conversations').json['data']['conversations'] == []
    assert api.send('GET', '/agent/missing').status_code == 404
    assert api.send('DELETE', '/agent/conversations').status_code == 405
    assert api.send('GET', '/agent/conversations/fake/history').status_code == 404
    assert not api.state.calls


def test_conflict_user_busy_and_real_admission_gate(api):
    cid, rid = create(api), uuid4().hex
    seed_turn(api, cid, rid)
    assert send_message(api, cid, rid, 'different').status_code == 409
    assert send_message(api, cid).status_code == 409
    assert send_message(api, create(api)).status_code == 409
    assert not api.state.calls


def test_real_rate_limit_and_missing_gate(api):
    cid = create(api)
    for _ in range(3):
        assert send_message(api, cid).status_code == 200
    response = send_message(api, cid)
    assert response.status_code == 429 and response.json['error']['code'] == 'rate_limited'
    assert len(api.state.calls) == 3 and int(response.headers['Retry-After']) > 0
    db.session.execute(sa.delete(AgentAdmissionGate))
    db.session.commit()
    assert send_message(api, cid).status_code == 503
    assert len(api.state.calls) == 3


def test_real_global_capacity(api):
    cid = create(api)
    seed_turn(api, cid, uuid4().hex)
    with api.app.test_request_context():
        login_user(api.users[1])
        other = c.create_conversation()['id']
        c.begin_turn(other, uuid4().hex, 'question')
    third = api.browser(api.users[2])
    new_cid = third('POST', '/agent/conversations', json={}).json['data']['conversation_id']
    response = third('POST', f'/agent/conversations/{new_cid}/messages',
                     json={'request_id': uuid4().hex, 'question': 'question'})
    assert response.status_code == 429 and not api.state.calls


def test_uncertain_begin_does_not_retry_or_release(api, monkeypatch):
    cid, rid = create(api), uuid4().hex
    original = c.begin_admitted_turn

    def uncertain(*args):
        original(*args)
        raise c.ConversationError('chat_persistence_failed')
    monkeypatch.setattr(c, 'begin_admitted_turn', uncertain)
    response = send_message(api, cid, rid)
    assert response.status_code == 503 and response.json['error']['code'] == 'request_state_unresolved'
    result = api.send('GET', f'/agent/conversations/{cid}/requests/{rid}').json['data']
    assert result['status'] == 'pending' and result['request_id'] == rid
    assert not api.state.calls
    assert db.session.scalar(sa.select(sa.func.count()).select_from(ChatTurn)) == 1


def test_completed_corruption_and_exceptions_are_safe(api, monkeypatch):
    cid, rid = create(api), uuid4().hex
    seed_turn(api, cid, rid, 'completed')
    db.session.execute(sa.delete(ChatMessage).where(ChatMessage.role == 'assistant'))
    db.session.commit()
    assert api.send('GET', f'/agent/conversations/{cid}/requests/{rid}').json['error']['code'] == 'result_unavailable'
    monkeypatch.setattr(c, 'list_conversations', Mock(side_effect=RuntimeError('secret-url private SQL key')))
    response = api.send('GET', '/agent/conversations')
    assert response.status_code == 500 and 'secret-url' not in response.get_data(as_text=True)


def test_model_failure_persists_failed_without_raw_exception(api):
    cid = create(api)
    api.state.replies = [RuntimeError('secret-key SQL')]
    response = send_message(api, cid)
    assert response.json['data']['status'] == 'failed'
    assert response.json['data']['answer'] is None
    assert 'secret-key' not in response.get_data(as_text=True)


def test_created_time_is_utc_and_options_is_json(api):
    response = api.send('POST', '/agent/conversations', json={})
    row = db.session.get(Conversation, response.json['data']['conversation_id'])
    expected = row.created_at.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')
    assert response.json['data']['created_at'] == expected
    assert api.send('OPTIONS', '/agent/conversations').status_code == 405


@pytest.mark.parametrize('kind', ['service', 'database'])
def test_database_unavailable_is_json_503(api, monkeypatch, kind):
    exc = (c.ConversationError('chat_persistence_failed') if kind == 'service' else
           sa.exc.OperationalError('private SQL URL key', {}, Exception('private credentials')))
    monkeypatch.setattr(c, 'list_conversations', Mock(side_effect=exc))
    response = api.send('GET', '/agent/conversations')
    assert response.status_code == 503 and response.json['error']['code'] == 'chat_persistence_failed'
    assert 'private' not in response.get_data(as_text=True) and not api.state.calls


def test_missing_request_and_bad_request_id_no_model(api):
    cid = create(api)
    assert api.send('GET', f'/agent/conversations/{cid}/requests/{uuid4().hex}').status_code == 404
    assert api.send('GET', f'/agent/conversations/{cid}/requests/bad').status_code == 400
    assert not api.state.calls


def test_history_corruption_fails_closed(api):
    cid, rid = create(api), uuid4().hex
    seed_turn(api, cid, rid, 'completed')
    db.session.execute(sa.update(ChatMessage).where(ChatMessage.role == 'assistant').values(
        role='tool', tool_call_id='orphan', tool_name='list_user_habits'))
    db.session.commit()
    before = snapshot()
    response = api.send('GET', f'/agent/conversations/{cid}/history')
    assert response.status_code == 503 and response.json['error']['code'] == 'result_unavailable'
    assert before == snapshot() and not api.state.calls


def test_timeout_never_changes_request_id_or_retries(api):
    cid, rid = create(api), uuid4().hex
    api.state.replies = [TimeoutError('private remote details')]
    response = send_message(api, cid, rid)
    assert response.json['data']['request_id'] == rid and response.json['data']['status'] == 'failed'
    assert send_message(api, cid, rid).json['data']['request_id'] == rid
    assert len(api.state.calls) == 1
    assert db.session.scalar(sa.select(sa.func.count()).select_from(ChatTurn)) == 1


@pytest.mark.parametrize('path', ['/agent/conversations', '/agent/missing'])
def test_unauthenticated_post_with_token_is_401(api, path):
    assert api.browser()('POST', path, json={}).status_code == 401


def test_summary_history_displays_counts_without_storage_ids(api):
    cid, rid = create(api), uuid4().hex
    api.state.replies = [call('get_checkin_summary', {'days': 7}, 'summary'), final()]
    assert send_message(api, cid, rid).status_code == 200
    before = snapshot()
    messages = api.send('GET', f'/agent/conversations/{cid}/history').json['data']['turns'][0]['messages']
    result = json.loads(messages[2]['content'])
    assert result['habits'] == [{'name': 'READ-A', 'checkins': 0}]
    assert result['metric'] == 'checkin_count_not_completion_rate'
    assert messages[2]['tool_call_id'] == messages[1]['tool_calls'][0]['id']
    assert snapshot() == before
    stored = db.session.scalar(sa.select(ChatMessage).where(ChatMessage.role == 'tool'))
    assert 'id' in json.loads(stored.content)['habits'][0]  # Original audit data is untouched.
