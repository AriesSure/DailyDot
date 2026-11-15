"""Shared real-connection scenarios for owned SQLite and fixed MySQL CI only."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import hashlib
from threading import Barrier
from uuid import uuid4

from flask_login import login_user
import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.agent import conversations as c, admission
from app.models import User, Conversation, ChatTurn, ChatMessage


def verify_concurrency(application, engine, *, verify_connection=None):
    from app.extensions import login
    if not hasattr(application, 'login_manager'):
        login.init_app(application)
    application.config['SECRET_KEY'] = 'synthetic-admission-test-only'
    digest = hashlib.sha256(b'question').hexdigest()
    physical = set()

    def scenario_users(n):
        with Session(engine, expire_on_commit=False) as session, session.begin():
            users = [User(username=uuid4().hex, email=uuid4().hex + '@example.invalid') for _ in range(n)]
            session.add_all(users)
            session.flush()
            conversations = [Conversation(user_id=user.id) for user in users]
            session.add_all(conversations)
            session.flush()
            return users, [row.id for row in conversations]

    def attempt(user, cid, rid, barrier=None, *, rollback=False):
        with application.test_request_context():
            login_user(user)
            with engine.connect() as connection:
                identifier = (connection.execute(sa.text('SELECT CONNECTION_ID()')).scalar_one()
                              if engine.dialect.name == 'mysql' else id(connection.connection.driver_connection))
                if verify_connection:
                    verify_connection(connection)
                connection.rollback()
                if barrier:
                    barrier.wait(timeout=20)
                try:
                    with Session(connection, expire_on_commit=False) as session:
                        with session.begin():
                            result = c._begin_with_session(
                                session, user.id, cid, rid, 'question', digest, admitted=True)
                            if rollback:
                                raise RuntimeError('synthetic_transaction_rollback')
                    return identifier, result['created']
                except c.ConversationError as exc:
                    return identifier, exc.args[0]

    def race(jobs):
        barrier = Barrier(len(jobs))
        with ThreadPoolExecutor(len(jobs)) as executor:
            futures = [executor.submit(attempt, *job, barrier) for job in jobs]
            values = [future.result(timeout=40) for future in futures]
        assert len({row[0] for row in values}) == len(jobs), 'admission_connections_not_independent'
        physical.update(row[0] for row in values)
        return [row[1] for row in values]

    def finish(user, cid, rid):
        with application.test_request_context():
            login_user(user)
            c.finish_turn(cid, rid, status='failed', messages=[], error_code='chat_execution_failed')

    users, ids = scenario_users(1)
    with Session(engine, expire_on_commit=False) as session, session.begin():
        other = Conversation(user_id=users[0].id)
        session.add(other)
        session.flush()
        ids.append(other.id)
    requests = [uuid4().hex, uuid4().hex]
    outcomes = race([(users[0], ids[i], requests[i]) for i in range(2)])
    assert outcomes.count(True) == 1 and outcomes.count('agent_user_busy') == 1
    for i, outcome in enumerate(outcomes):
        if outcome is True:
            finish(users[0], ids[i], requests[i])

    users, ids = scenario_users(3)
    requests = [uuid4().hex for _ in users]
    assert attempt(users[0], ids[0], requests[0])[1] is True
    # The first request is still pending, yet its gate transaction is over:
    # two independent connections can compete for the remaining global slot.
    outcomes = race([(users[i], ids[i], requests[i]) for i in (1, 2)])
    assert outcomes.count(True) == 1 and outcomes.count('agent_global_capacity') == 1
    finish(users[0], ids[0], requests[0])
    for i, outcome in zip((1, 2), outcomes):
        if outcome is True:
            finish(users[i], ids[i], requests[i])

    users, ids = scenario_users(1)
    rid = uuid4().hex
    outcomes = race([(users[0], ids[0], rid)] * 2)
    assert sorted(outcomes) == [False, True]
    finish(users[0], ids[0], rid)

    users, ids = scenario_users(1)
    # Three recent accepted turns including failed attempts consume the minute
    # allowance. Race the last available slot after two committed completions.
    for _ in range(2):
        rid = uuid4().hex
        assert attempt(users[0], ids[0], rid)[1] is True
        finish(users[0], ids[0], rid)
    requests = [uuid4().hex, uuid4().hex]
    outcomes = race([(users[0], ids[0], rid) for rid in requests])
    assert outcomes.count(True) == 1
    # The competing request may see the pending owner first; after finishing,
    # the same rejected ID must be refused by the rolling frequency limit.
    winner = outcomes.index(True)
    finish(users[0], ids[0], requests[winner])
    assert attempt(users[0], ids[0], requests[1-winner])[1] == 'agent_rate_limited'

    # Synthetic old accepted turns use database UTC, not Worker wall clocks.
    # They are all outside the minute window and inside the hour window.
    users, ids = scenario_users(1)
    with Session(engine) as session, session.begin():
        now = admission.database_utc(session)
        session.get(Conversation, ids[0]).next_turn_no = 21
        for i in range(20):
            stamp = now - timedelta(seconds=121 + i * 61)
            status = ('pending', 'completed', 'failed', 'interrupted')[i % 4]
            row = ChatTurn(conversation_id=ids[0], turn_no=i + 1, client_request_id=uuid4().hex,
                           request_digest=digest, status=status, created_at=stamp)
            session.add(row)
            session.flush()
            session.add(ChatMessage(turn_id=row.id, position=0, role='user', content='question', created_at=stamp))
            if status == 'completed':
                session.add(ChatMessage(turn_id=row.id, position=1, role='assistant', content='synthetic answer',
                                        created_at=stamp))
    assert attempt(users[0], ids[0], uuid4().hex)[1] == 'agent_rate_limited'
    with Session(engine) as session, session.begin():
        oldest = session.scalar(sa.select(ChatTurn).where(ChatTurn.conversation_id == ids[0])
                                .order_by(ChatTurn.created_at).limit(1))
        oldest.created_at = admission.database_utc(session) - timedelta(seconds=3600)
    requests = [uuid4().hex, uuid4().hex]
    outcomes = race([(users[0], ids[0], rid) for rid in requests])
    assert outcomes.count(True) == 1
    winner = outcomes.index(True)
    finish(users[0], ids[0], requests[winner])
    assert attempt(users[0], ids[0], requests[1-winner])[1] == 'agent_rate_limited'

    users, ids = scenario_users(1)
    rid = uuid4().hex
    try:
        attempt(users[0], ids[0], rid, rollback=True)
    except RuntimeError as error:
        assert str(error) == 'synthetic_transaction_rollback'
    else:
        raise AssertionError('admission_rollback_missing')
    with application.test_request_context():
        login_user(users[0])
        assert c.get_request(ids[0], rid) is None
        assert c.get_conversation(ids[0])['active_request_id'] is None
    assert attempt(users[0], ids[0], rid)[1] is True
    finish(users[0], ids[0], rid)
    assert len(physical) >= 2
    return dict.fromkeys(('same_user', 'global_slot', 'duplicate', 'rate', 'rollback',
                          'gate_released', 'independent_connections'), True)
