"""Admission and public results on isolated SQLite, no model requests."""
from datetime import datetime, timezone, timedelta
from uuid import uuid4
from unittest.mock import Mock

import pytest
import sqlalchemy as sa
from flask_login import login_user

from app.agent import admission as a, conversations as c
from app.models import AgentAdmissionGate, User, Conversation, ChatTurn, ChatMessage


@pytest.fixture
def setup(app, db, monkeypatch):
    now = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(a, 'database_utc', lambda session: now)
    db.session.add(AgentAdmissionGate(gate_key='global', lock_version=0))
    users = [User(username=str(i), email=f'{i}@example.invalid') for i in range(3)]
    db.session.add_all(users)
    db.session.commit()
    with app.test_request_context():
        login_user(users[0])
        yield db, users, now


def begin(cid):
    rid = uuid4().hex
    return rid, c.begin_admitted_turn(cid, rid, 'question')


def done(cid, rid, status='completed'):
    return c.finish_turn(cid, rid, status=status,
                         messages=[{'role': 'assistant', 'content': '<b>answer</b>'}] if status == 'completed' else [])


@pytest.mark.parametrize('status', ['pending', 'completed', 'failed', 'interrupted'])
def test_result_statuses_public_and_read_only(setup, monkeypatch, status):
    db, _, _ = setup
    cid = c.create_conversation()['id']
    rid, _ = begin(cid)
    if status != 'pending':
        done(cid, rid, status)
    before = c.history(cid), c.get_conversation(cid), db.session.get(AgentAdmissionGate, 'global').lock_version
    model = Mock(side_effect=AssertionError('read cannot execute model'))
    monkeypatch.setattr('app.agent.service.run_agent_with_history', model)
    monkeypatch.setattr('app.agent.dialogue.run_agent_with_history', model)
    monkeypatch.setattr('app.agent.service.query_tool', model)
    result = c.get_request_result(cid, rid)
    assert result == c.get_request_result(cid, rid)
    assert set(result) == {'conversation_id', 'request_id', 'turn_no', 'status', 'answer', 'failure',
                           'created_at', 'completed_at', 'history_truncated'}
    assert result['status'] == status and result['history_truncated'] is None
    assert result['answer'] == ('<b>answer</b>' if status == 'completed' else None)
    assert bool(result['failure']) == (status in ('failed', 'interrupted'))
    assert result['created_at'].endswith('Z')
    assert before == (c.history(cid), c.get_conversation(cid),
                      db.session.get(AgentAdmissionGate, 'global').lock_version)
    model.assert_not_called()


def test_result_not_found_and_foreign_owner(setup):
    _, users, _ = setup
    cid = c.create_conversation()['id']
    rid, _ = begin(cid)
    for owner, request in [(users[0], uuid4().hex), (users[1], rid)]:
        login_user(owner)
        with pytest.raises(c.ConversationError, match='chat_not_found'):
            c.get_request_result(cid, request)


@pytest.mark.parametrize('corrupt', ['missing', 'orphan', 'position', 'user_system', 'json', 'timestamp'])
def test_completed_corruption_not_repaired(setup, corrupt):
    db, _, _ = setup
    cid = c.create_conversation()['id']
    rid, _ = begin(cid)
    done(cid, rid)
    row = db.session.scalar(sa.select(ChatTurn).where(ChatTurn.client_request_id == rid))
    messages = db.session.scalars(sa.select(ChatMessage).where(ChatMessage.turn_id == row.id)
                                  .order_by(ChatMessage.position)).all()
    if corrupt == 'missing':
        db.session.delete(messages[-1])
    elif corrupt == 'orphan':
        messages[-1].role, messages[-1].tool_call_id = 'tool', 'missing'
    elif corrupt == 'position':
        messages[-1].position = 3
    elif corrupt == 'user_system':
        messages[0].role = 'assistant'
    elif corrupt == 'json':
        db.session.execute(sa.text("UPDATE chat_messages SET tool_calls='{invalid' WHERE id=:id"),
                           {'id': messages[-1].id})
    else:
        db.session.execute(sa.text("UPDATE chat_messages SET created_at='invalid' WHERE id=:id"),
                           {'id': messages[-1].id})
    db.session.commit()
    with pytest.raises(c.ConversationError, match='chat_result_unavailable'):
        c.get_request_result(cid, rid)
    assert c.get_request(cid, rid)['status'] == 'completed'


def test_active_user_global_slots_and_idempotency(setup):
    _, users, _ = setup
    first = c.create_conversation()['id']
    rid, row = begin(first)
    assert not c.begin_admitted_turn(first, rid, 'question')['created']
    with pytest.raises(c.ConversationError, match='chat_request_conflict'):
        c.begin_admitted_turn(first, rid, 'different')
    other = c.create_conversation()['id']
    with pytest.raises(c.ConversationError, match='agent_user_busy'):
        begin(other)
    login_user(users[1])
    second = c.create_conversation()['id']
    second_rid, _ = begin(second)
    login_user(users[2])
    third = c.create_conversation()['id']
    with pytest.raises(c.ConversationError, match='agent_global_capacity'):
        begin(third)
    assert c.history(third) == []
    login_user(users[1])
    done(second, second_rid, 'failed')
    login_user(users[2])
    begin(third)
    assert row['turn']['status'] == 'pending'


@pytest.mark.parametrize('status', ['completed', 'failed', 'interrupted'])
def test_minute_limit_and_exact_boundary(setup, monkeypatch, status):
    _, _, now = setup
    cid = c.create_conversation()['id']
    for _ in range(3):
        rid, _ = begin(cid)
        done(cid, rid, status)
    with pytest.raises(c.ConversationError, match='agent_rate_limited') as error:
        begin(cid)
    assert error.value.retry_after_seconds == 60 and len(c.history(cid)) == 3
    monkeypatch.setattr(a, 'database_utc', lambda session: now + timedelta(seconds=59, microseconds=999999))
    with pytest.raises(c.ConversationError, match='agent_rate_limited') as error:
        begin(cid)
    assert error.value.retry_after_seconds == 1
    monkeypatch.setattr(a, 'database_utc', lambda session: now + timedelta(seconds=60))
    begin(cid)  # Window is (now-T, now]; exact lower boundary expires.


def test_hour_limit_and_expiry(setup, monkeypatch):
    _, _, now = setup
    cid = c.create_conversation()['id']
    for i in range(20):
        monkeypatch.setattr(a, 'database_utc', lambda session, i=i: now + timedelta(seconds=i * 61))
        rid, _ = begin(cid)
        done(cid, rid, 'failed')
    monkeypatch.setattr(a, 'database_utc', lambda session: now + timedelta(seconds=20 * 61))
    with pytest.raises(c.ConversationError, match='agent_rate_limited'):
        begin(cid)
    monkeypatch.setattr(a, 'database_utc', lambda session: now + timedelta(seconds=3600))
    begin(cid)


def test_missing_gate_fails_closed_and_no_auto_seed(setup):
    db, _, _ = setup
    cid = c.create_conversation()['id']
    db.session.execute(sa.delete(AgentAdmissionGate))
    db.session.commit()
    with pytest.raises(c.ConversationError, match='agent_admission_unavailable'):
        begin(cid)
    assert c.history(cid) == []
    assert db.session.scalar(sa.select(sa.func.count()).select_from(AgentAdmissionGate)) == 0


def test_create_failure_rolls_back_gate_slot_and_frequency(setup, monkeypatch):
    from sqlalchemy.orm import Session
    db, _, _ = setup
    cid = c.create_conversation()['id']
    original = Session.flush

    def fail(session, *args, **kwargs):
        if any(isinstance(row, ChatTurn) for row in session.new):
            raise sa.exc.OperationalError('private', {}, Exception('private'))
        return original(session, *args, **kwargs)

    monkeypatch.setattr(Session, 'flush', fail)
    with pytest.raises(c.ConversationError, match='chat_persistence_failed'):
        begin(cid)
    assert c.history(cid) == [] and c.get_conversation(cid)['active_request_id'] is None
    assert db.session.get(AgentAdmissionGate, 'global').lock_version == 0


def test_clock_backwards_and_stale_active_never_reclaimed(setup, monkeypatch):
    db, _, now = setup
    cid = c.create_conversation()['id']
    rid, _ = begin(cid)
    db.session.execute(sa.update(Conversation).where(Conversation.id == cid).values(
        active_started_at=now - timedelta(days=100)))
    db.session.commit()
    with pytest.raises(c.ConversationError, match='agent_user_busy'):
        begin(c.create_conversation()['id'])
    done(cid, rid)
    monkeypatch.setattr(a, 'database_utc', lambda session: now - timedelta(seconds=1))
    with pytest.raises(c.ConversationError, match='agent_admission_unavailable'):
        begin(cid)


@pytest.mark.parametrize('status', ['pending', 'completed', 'failed', 'interrupted'])
def test_all_legacy_turn_statuses_consume_rate(setup, status):
    db, _, now = setup
    cid = c.create_conversation()['id']
    db.session.add_all([ChatTurn(conversation_id=cid, turn_no=i + 1, client_request_id=uuid4().hex,
                                 request_digest='a' * 64, status=status, created_at=now) for i in range(3)])
    db.session.commit()
    with pytest.raises(c.ConversationError, match='agent_rate_limited'):
        begin(cid)
    assert len(c.history(cid)) == 3


def test_result_reads_do_not_require_healthy_gate(setup):
    db, _, _ = setup
    cid = c.create_conversation()['id']
    rid, _ = begin(cid)
    db.session.execute(sa.delete(AgentAdmissionGate))
    db.session.commit()
    assert c.get_request_result(cid, rid)['status'] == 'pending'


def test_gate_update_failure_is_safe_persistence_error(setup, monkeypatch):
    from sqlalchemy.orm import Session
    _, _, _ = setup
    cid = c.create_conversation()['id']
    original = Session.execute

    def fail(session, statement, *args, **kwargs):
        if isinstance(statement, sa.sql.dml.Update) and statement.table.name == 'agent_admission_gate':
            raise sa.exc.OperationalError('private', {}, Exception('private'))
        return original(session, statement, *args, **kwargs)

    monkeypatch.setattr(Session, 'execute', fail)
    with pytest.raises(c.ConversationError, match='chat_persistence_failed'):
        begin(cid)
    assert c.history(cid) == [] and c.get_conversation(cid)['active_request_id'] is None


@pytest.mark.parametrize('key,version', [('GLOBAL', 0), ('global ', 0), ('global', -1)])
def test_gate_database_checks_are_enforced(setup, key, version):
    db, _, _ = setup
    db.session.execute(sa.delete(AgentAdmissionGate))
    db.session.commit()
    with pytest.raises(sa.exc.IntegrityError):
        db.session.execute(sa.insert(AgentAdmissionGate).values(gate_key=key, lock_version=version))
        db.session.commit()
    db.session.rollback()
    assert db.session.scalar(sa.select(sa.func.count()).select_from(AgentAdmissionGate)) == 0
