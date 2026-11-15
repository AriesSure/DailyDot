"""Service tests use isolated SQLite and trusted Flask-Login request contexts."""
from contextlib import contextmanager
from uuid import uuid4

import pytest
import sqlalchemy as sa
from flask_login import login_user
from sqlalchemy.orm import Session

from app.agent import conversations as c
from app.models import User, ChatTurn


@pytest.fixture
def users(db):
    rows = [User(username=name, email=f'{name}@example.invalid') for name in ('one', 'two')]
    db.session.add_all(rows)
    db.session.commit()
    return rows


@contextmanager
def logged(app, user):
    with app.test_request_context():
        login_user(user)
        yield


def test_auth(app):
    with app.test_request_context(), pytest.raises(c.ConversationError, match='authentication'):
        c.create_conversation()


def test_ownership_pages(app, users):
    with logged(app, users[0]):
        ids = [c.create_conversation()['id'] for _ in range(3)]
        assert c.get_conversation(ids[0])['title'] == '新对话'
        rows = c.list_conversations()
        assert c.list_conversations(limit=1, offset=1) == rows[1:2]
    with logged(app, users[1]):
        assert c.list_conversations() == []
        for action in (c.get_conversation, c.history):
            with pytest.raises(c.ConversationError, match='not_found'):
                action(ids[0])


def test_idempotent_and_lifecycle(app, users):
    with logged(app, users[0]):
        cid, rid = c.create_conversation()['id'], uuid4().hex
        first = c.begin_turn(cid, rid, ' hi\r\nthere ')
        assert first['created'] and first['turn']['turn_no'] == 1
        assert not c.begin_turn(cid, rid, 'hi\nthere')['created']
        with pytest.raises(c.ConversationError, match='conflict'):
            c.begin_turn(cid, rid, 'changed')
        with pytest.raises(c.ConversationError, match='busy'):
            c.begin_turn(cid, uuid4().hex, 'next')
        c.finish_turn(cid, rid, status='completed', messages=[{'role': 'assistant', 'content': 'done'}])
        assert not c.begin_turn(cid, rid, 'hi\nthere')['created']
        assert c.get_conversation(cid)['active_request_id'] is None
        assert len(c.history(cid, completed_only=True)[0]['messages']) == 2
        with pytest.raises(c.ConversationError, match='ownership_lost'):
            c.finish_turn(cid, rid, status='failed', messages=[])
        other = uuid4().hex
        assert c.begin_turn(cid, other, 'again')['turn']['turn_no'] == 2
        c.finish_turn(cid, other, status='interrupted', messages=[], error_code='chat_execution_interrupted')
        assert [r['turn_no'] for r in c.history(cid)] == [1, 2]
        assert len(c.history(cid, completed_only=True)) == 1
        assert c.get_request(cid, other)['status'] == 'interrupted'


@pytest.mark.parametrize('messages', [
    [], [{'role': 'system', 'content': 'bad'}], [{'role': 'tool', 'content': 'orphan', 'tool_call_id': 'x'}],
    [{'role': 'assistant', 'tool_calls': [
        {'id': 'x', 'type': 'function', 'function': {'name': 'list_user_habits', 'arguments': '{}'}}]}],
])
def test_incomplete_success_keeps_ownership(app, users, messages):
    with logged(app, users[0]):
        cid, rid = c.create_conversation()['id'], uuid4().hex
        c.begin_turn(cid, rid, 'question')
        with pytest.raises(c.ConversationError):
            c.finish_turn(cid, rid, status='completed', messages=messages)
        assert c.get_request(cid, rid)['status'] == 'pending'
        assert c.get_conversation(cid)['active_request_id'] == rid


def test_protocol_and_failure(app, users):
    calls = [{'id': 'x', 'type': 'function', 'function': {'name': 'list_user_habits', 'arguments': '{}'}}]
    messages = [{'role': 'assistant', 'tool_calls': calls},
                {'role': 'tool', 'content': '{}', 'tool_call_id': 'x', 'tool_name': 'list_user_habits'},
                {'role': 'assistant', 'content': 'answer'}]
    with logged(app, users[0]):
        cid, rid = c.create_conversation()['id'], uuid4().hex
        c.begin_turn(cid, rid, 'question')
        c.finish_turn(cid, rid, status='completed', messages=messages)
        saved = c.history(cid)[0]['messages']
        assert [m['position'] for m in saved] == [0, 1, 2, 3]
        assert saved[1]['tool_calls'] == calls and saved[2]['tool_call_id'] == 'x'
        rid = uuid4().hex
        c.begin_turn(cid, rid, 'failure')
        c.finish_turn(cid, rid, status='failed', messages=messages[:1], error_code='chat_execution_failed')
        assert len(c.history(cid, completed_only=True)) == 1
        other = c.create_conversation()['id']
        assert c.history(other) == []
        c.begin_turn(other, uuid4().hex, 'independent')


def test_rollback_does_not_touch_business_session(app, users, db, monkeypatch):
    with logged(app, users[0]):
        cid = c.create_conversation()['id']
        rid = uuid4().hex
        original = Session.flush

        def fail(session, *args, **kwargs):
            if any(isinstance(row, ChatTurn) for row in session.new):
                raise sa.exc.OperationalError('private', {}, Exception('private'))
            return original(session, *args, **kwargs)

        monkeypatch.setattr(Session, 'flush', fail)
        with pytest.raises(c.ConversationError, match='persistence_failed'):
            c.begin_turn(cid, rid, 'question')
        assert c.get_conversation(cid)['next_turn_no'] == 1
        assert c.get_conversation(cid)['active_request_id'] is None


@pytest.mark.parametrize('rid', ['', 'ABC' * 11, 'z' * 32, None, True])
def test_invalid_request(app, users, rid):
    with logged(app, users[0]):
        cid = c.create_conversation()['id']
        with pytest.raises(c.ConversationError, match='request_invalid'):
            c.begin_turn(cid, rid, 'question')
        assert c.history(cid) == []


def test_late_completion_cannot_release_next_request(app, users):
    with logged(app, users[0]):
        cid, first, second = c.create_conversation()['id'], uuid4().hex, uuid4().hex
        c.begin_turn(cid, first, 'one')
        c.finish_turn(cid, first, status='failed', messages=[])
        c.begin_turn(cid, second, 'two')
        with pytest.raises(c.ConversationError, match='ownership_lost'):
            c.finish_turn(cid, first, status='completed', messages=[{'role': 'assistant', 'content': 'late'}])
        assert c.get_conversation(cid)['active_request_id'] == second
        assert c.get_request(cid, first)['status'] == 'failed'
        with pytest.raises(c.ConversationError, match='transition_invalid'):
            c.finish_turn(cid, second, status='pending', messages=[])


@pytest.mark.parametrize('messages', [
    [{'role': 'assistant', 'content': 'x', 'secret': 'extra'}],
    [{'role': 'assistant', 'tool_calls': [{'id': 'a', 'type': 'function', 'function': {
        'name': 'list_user_habits', 'arguments': '{"user_id":2}'}}]}],
    [{'role': 'assistant', 'content': object()}],
])
def test_invalid_protocol_safe_error(messages):
    with pytest.raises(c.ConversationError, match='protocol_invalid'):
        c.validated_messages(messages, True)


def test_file_sqlite_independent_connections():
    from tests.test_migration_validation import child
    child("""
from tools import migration_validation as v
import sqlalchemy as sa
from sqlalchemy.orm import Session
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
from app.agent import conversations as c
from app.models import User, Conversation, ChatTurn, ChatMessage
with v.create_temporary_sqlite_target() as target:
    with v.SQLiteBoundary(target):
        application = v.create_minimal_migration_app(target)
        from app.extensions import db
        with application.app_context():
            engine = db.engine
        tables = [User.__table__, Conversation.__table__, ChatTurn.__table__, ChatMessage.__table__]
        User.metadata.create_all(engine, tables=tables)
        with Session(engine) as session:
            user = User(username='file', email='file@example.invalid')
            session.add(user)
            session.flush()
            convo = Conversation(user_id=user.id)
            session.add(convo)
            session.commit()
            uid, cid = user.id, convo.id
        c.identity = lambda: uid
        c.db = type('Database', (), {'engine': engine})()

        def begin(_):
            try:
                return c.begin_turn(cid, uuid4().hex, 'test')['created']
            except c.ConversationError as exc:
                assert str(exc) == 'chat_busy'
                return False

        try:
            with ThreadPoolExecutor(2) as pool:
                assert sorted(pool.map(begin, range(2))) == [False, True]
            with engine.connect() as first, engine.connect() as second:
                for connection in (first, second):
                    assert connection.scalar(sa.text('PRAGMA foreign_keys')) == 1
            with engine.begin() as connection:
                connection.execute(sa.delete(Conversation))
                assert connection.scalar(sa.select(sa.func.count()).select_from(ChatTurn)) == 0
                assert connection.scalar(sa.select(sa.func.count()).select_from(ChatMessage)) == 0
        finally:
            engine.dispose()
""")


def tool_messages(cid='call_abc', name='list_user_habits'):
    return [{'role': 'assistant', 'tool_calls': [
        {'id': cid, 'type': 'function', 'function': {'name': name, 'arguments': '{}'}}]},
        {'role': 'tool', 'content': '{}', 'tool_call_id': cid, 'tool_name': name},
        {'role': 'assistant', 'content': 'done'}]


@pytest.mark.parametrize('cid', ['call_abc123', 'x' * 100])
def test_tool_id_valid_boundary(app, users, cid):
    with logged(app, users[0]):
        convo, rid = c.create_conversation()['id'], uuid4().hex
        c.begin_turn(convo, rid, 'hello')
        c.finish_turn(convo, rid, status='completed', messages=tool_messages(cid))
        assert c.history(convo)[0]['messages'][2]['tool_call_id'] == cid


@pytest.mark.parametrize('change', ['empty_id', 'int_id', 'long_id', 'type', 'missing_function',
                                    'empty_name', 'long_name', 'result_long_id', 'result_long_name'])
def test_tool_boundaries_rejected_before_persistence(app, users, change):
    messages = tool_messages()
    call = messages[0]['tool_calls'][0]
    if change in ('empty_id', 'int_id', 'long_id'):
        call['id'] = {'empty_id': '', 'int_id': 123, 'long_id': 'x' * 101}[change]
    elif change == 'type':
        call['type'] = 'other'
    elif change == 'missing_function':
        del call['function']
    elif change in ('empty_name', 'long_name'):
        call['function']['name'] = '' if change == 'empty_name' else 'x' * 65
    else:
        messages[1]['tool_call_id' if change == 'result_long_id' else 'tool_name'] = 'x' * 101
    with logged(app, users[0]):
        convo, rid = c.create_conversation()['id'], uuid4().hex
        c.begin_turn(convo, rid, 'hello')
        with pytest.raises(c.ConversationError, match='protocol_invalid'):
            c.finish_turn(convo, rid, status='completed', messages=messages)
        assert len(c.history(convo)[0]['messages']) == 1
        assert c.get_request(convo, rid)['status'] == 'pending'


@pytest.mark.parametrize('count,allowed', [(31, True), (32, False)])
def test_entire_turn_message_count(app, users, count, allowed):
    with logged(app, users[0]):
        convo, rid = c.create_conversation()['id'], uuid4().hex
        c.begin_turn(convo, rid, 'hello')
        messages = [{'role': 'assistant', 'content': 'ok'} for _ in range(count)]
        if allowed:
            c.finish_turn(convo, rid, status='completed', messages=messages)
            assert len(c.history(convo)[0]['messages']) == 32
        else:
            with pytest.raises(c.ConversationError, match='protocol_invalid'):
                c.finish_turn(convo, rid, status='completed', messages=messages)
            assert len(c.history(convo)[0]['messages']) == 1


@pytest.mark.parametrize('overflow', [0, 1])
def test_entire_turn_json_exact_boundary(app, users, overflow):
    import json
    user = '合成用户' * 100
    protocol = [{'role': role, 'content': content, 'tool_calls': None,
                 'tool_call_id': None, 'tool_name': None, 'position': position}
                for position, (role, content) in enumerate([('user', user), ('assistant', '')])]
    overhead = len(json.dumps(protocol, ensure_ascii=False, allow_nan=False))
    answer = '答' * (48000 - overhead + overflow)
    with logged(app, users[0]):
        convo, rid = c.create_conversation()['id'], uuid4().hex
        c.begin_turn(convo, rid, user)
        messages = [{'role': 'assistant', 'content': answer}]
        if overflow:
            with pytest.raises(c.ConversationError, match='protocol_invalid'):
                c.finish_turn(convo, rid, status='completed', messages=messages)
            assert len(c.history(convo)[0]['messages']) == 1
            assert c.get_conversation(convo)['active_request_id'] == rid
            assert c.get_request(convo, rid)['status'] == 'pending'
        else:
            c.finish_turn(convo, rid, status='completed', messages=messages)
            assert c.history(convo)[0]['messages'][-1]['content'] == answer


def test_user_only_budget_and_begin_rejects_oversize(app, users):
    with pytest.raises(c.ConversationError, match='protocol_invalid'):
        c.check_message_budget([{'role': 'user', 'content': 'x' * 48000}])
    with logged(app, users[0]):
        convo = c.create_conversation()['id']
        with pytest.raises(c.ConversationError, match='content_invalid'):
            c.begin_turn(convo, uuid4().hex, 'x' * 48000)
        assert c.history(convo) == []
        assert c.get_conversation(convo)['active_request_id'] is None
