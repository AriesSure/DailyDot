"""Database-serialized admission, called only inside the turn creation transaction."""
from datetime import datetime, timezone, timedelta
import math

import sqlalchemy as sa

from app.models import AgentAdmissionGate, Conversation, ChatTurn

USER_ACTIVE = 1
GLOBAL_ACTIVE = 2
WINDOWS = ((60, 3), (3600, 20))


def database_utc(session):
    # Match DATETIME(0): use one whole-second database value for checks and writes.
    dialect = session.bind.dialect.name
    if dialect == 'mysql':
        value = session.execute(sa.text('SELECT UTC_TIMESTAMP()')).scalar_one()
    elif dialect == 'sqlite':
        value = session.execute(sa.text("SELECT strftime('%Y-%m-%d %H:%M:%S','now')")).scalar_one()
    else:
        from app.agent.conversations import ConversationError
        raise ConversationError('agent_admission_unavailable')
    value = datetime.fromisoformat(value) if isinstance(value, str) else value
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def acquire(session):
    from app.agent.conversations import require
    changed = session.execute(sa.update(AgentAdmissionGate).where(
        AgentAdmissionGate.gate_key == 'global').values(lock_version=AgentAdmissionGate.lock_version + 1))
    require(changed.rowcount == 1, 'agent_admission_unavailable')
    gates = session.scalars(sa.select(AgentAdmissionGate)).all()
    require(len(gates) == 1 and gates[0].gate_key == 'global' and type(gates[0].lock_version) is int
            and gates[0].lock_version >= 0,
            'agent_admission_unavailable')


def check(session, uid):
    from app.agent.conversations import ConversationError, require
    now = database_utc(session)  # After obtaining the write lock, never a Worker clock.
    active = sa.select(sa.func.count()).select_from(Conversation).where(
        Conversation.active_request_id.is_not(None))
    require(session.scalar(active.where(Conversation.user_id == uid)) < USER_ACTIVE, 'agent_user_busy')
    require(session.scalar(active) < GLOBAL_ACTIVE, 'agent_global_capacity')
    base = sa.select(ChatTurn.created_at).join(Conversation).where(Conversation.user_id == uid)
    # Host-generated legacy timestamps in the future are not silently ignored.
    require(session.scalar(base.where(ChatTurn.created_at > now).limit(1)) is None,
            'agent_admission_unavailable')
    retry = 0
    for seconds, maximum in WINDOWS:
        dates = session.scalars(base.where(ChatTurn.created_at > now - timedelta(seconds=seconds))
                                .order_by(ChatTurn.created_at.desc()).limit(maximum)).all()
        if len(dates) >= maximum:
            retry = max(retry, math.ceil((dates[-1] + timedelta(seconds=seconds) - now).total_seconds()))
    if retry:
        error = ConversationError('agent_rate_limited')
        error.retry_after_seconds = max(1, retry)
        raise error
    return now
