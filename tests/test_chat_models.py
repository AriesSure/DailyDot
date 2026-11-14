"""Persistence tests on isolated SQLite; no model API calls."""
from datetime import datetime, timezone, timedelta
import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from app.models import User, Conversation, ChatTurn, ChatMessage


@pytest.fixture
def chat(db):
    db.session.execute(sa.text('PRAGMA foreign_keys=ON'))
    assert db.session.execute(sa.text('PRAGMA foreign_keys')).scalar_one() == 1
    user = User(username='chat', email='chat@example.invalid')
    db.session.add(user)
    db.session.commit()
    convo = Conversation(user_id=user.id)
    db.session.add(convo)
    db.session.commit()
    turn = ChatTurn(conversation_id=convo.id, turn_no=1, client_request_id='a'*32, request_digest='b'*64)
    db.session.add(turn)
    db.session.commit()
    return db, user.id, convo.id, turn.id


def test_defaults_json_utc(chat):
    db, _, cid, tid = chat
    convo = db.session.get(Conversation, cid)
    assert len(cid) == 32 and int(cid, 16) >= 0
    assert convo.title == '新对话' and convo.next_turn_no == 1
    assert convo.updated_at.utcoffset() == timedelta(0)
    assert db.session.get(ChatTurn, tid).status == 'pending'
    calls = [{'id': 'one', 'type': 'function', 'function': {'name': 'list_user_habits', 'arguments': '{}'}}]
    msg = ChatMessage(turn_id=tid, position=0, role='assistant', tool_calls=calls,
                      created_at=datetime(2025, 1, 1, 9, tzinfo=timezone(timedelta(hours=8))))
    db.session.add(msg)
    db.session.commit()
    mid = msg.id
    db.session.expunge_all()
    msg = db.session.get(ChatMessage, mid)
    assert msg.tool_calls == calls and msg.content is None
    assert msg.created_at == datetime(2025, 1, 1, 1, tzinfo=timezone.utc)


@pytest.mark.parametrize('status', ['pending', 'completed', 'failed', 'interrupted'])
def test_valid_status(chat, status):
    db, _, _, tid = chat
    db.session.get(ChatTurn, tid).status = status
    db.session.commit()
    assert db.session.get(ChatTurn, tid).status == status


@pytest.mark.parametrize('sql', [
    "UPDATE chat_turns SET status='bad'", "UPDATE chat_turns SET status='PENDING'",
    'UPDATE chat_turns SET status=NULL', 'UPDATE chat_turns SET turn_no=0',
    'UPDATE conversations SET title=NULL', 'UPDATE conversations SET next_turn_no=0',
    "UPDATE chat_turns SET conversation_id='missing'",
])
def test_constraints(chat, sql):
    db, *_ = chat
    with pytest.raises(IntegrityError):
        db.session.execute(sa.text(sql))
        db.session.commit()
    db.session.rollback()


@pytest.mark.parametrize('role', ['system', 'USER', 'bad', None])
def test_roles(chat, role):
    db, _, _, tid = chat
    db.session.add(ChatMessage(turn_id=tid, position=0, role=role))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


@pytest.mark.parametrize('kind', ['turn_no', 'request_id', 'position'])
def test_uniques(chat, kind):
    db, _, cid, tid = chat
    if kind == 'position':
        db.session.add(ChatMessage(turn_id=tid, position=0, role='user'))
        db.session.commit()
        row = ChatMessage(turn_id=tid, position=0, role='assistant')
    else:
        row = ChatTurn(conversation_id=cid, turn_no=1 if kind == 'turn_no' else 2,
                       client_request_id='c'*32 if kind == 'turn_no' else 'a'*32, request_digest='d'*64)
    db.session.add(row)
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


@pytest.mark.parametrize('field,value', [
    ('client_request_id', 'A'*32), ('client_request_id', 'bad'), ('request_digest', 'invalid'),
])
def test_hex(field, value):
    with pytest.raises(ValueError, match='chat_invalid_hex_identifier'):
        ChatTurn(**{field: value})


def test_raw_database_cascade_and_user_restrict(chat):
    db, uid, cid, tid = chat
    db.session.add(ChatMessage(turn_id=tid, position=0, role='tool', tool_call_id='one', content='{}'))
    db.session.commit()
    with pytest.raises(IntegrityError):
        db.session.execute(sa.delete(User).where(User.id == uid))
        db.session.commit()
    db.session.rollback()
    db.session.execute(sa.delete(Conversation).where(Conversation.id == cid))
    db.session.commit()
    assert db.session.query(ChatTurn).count() == db.session.query(ChatMessage).count() == 0
    assert db.session.get(User, uid) is not None


@pytest.mark.parametrize('calls', ['text', [{}], [{'id': 'one'}], [
    {'id': 'one', 'type': 'function', 'function': {'name': 'delete_habit', 'arguments': '{}'}}], [
    {'id': 'one', 'type': 'function', 'function': {'name': 'list_user_habits', 'arguments': 'null'}}]])
def test_tool_calls_wire_shape_rejected(calls):
    with pytest.raises(ValueError, match='chat_invalid_tool_calls'):
        ChatMessage(tool_calls=calls)
