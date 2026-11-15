"""Short, owned transactions for chat persistence; no model or tool execution.

Public methods derive identity from Flask-Login. They never commit/rollback the
application's scoped Session. Returned dictionaries are detached snapshots.
"""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import re
import unicodedata

from flask import has_request_context
from flask_login import current_user
import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.extensions import db
from app.models import Conversation, ChatTurn, ChatMessage, chat_utcnow
from app.agent.tools import validate, AgentError


class ConversationError(ValueError):
    """Only fixed public error codes leave this service."""


def require(condition, code):
    if not condition:
        raise ConversationError(code)


def identity():
    require(has_request_context() and current_user.is_authenticated, 'chat_authentication_required')
    return current_user.id


@contextmanager
def transaction():
    with Session(db.engine, expire_on_commit=False) as session:
        try:
            with session.begin():
                yield session
        except sa.exc.SQLAlchemyError:
            raise ConversationError('chat_persistence_failed') from None


def owned(session, uid, cid):
    row = session.scalar(sa.select(Conversation).where(Conversation.id == cid, Conversation.user_id == uid))
    require(row is not None, 'chat_not_found')
    return row


def snapshot(row):
    return {column.key: deepcopy(getattr(row, column.key)) for column in sa.inspect(type(row)).columns}


def request_id(value):
    require(type(value) is str and re.fullmatch('[0-9a-f]{32}', value) is not None, 'chat_request_invalid')


def normalize(content):
    require(type(content) is str, 'chat_content_invalid')
    content = unicodedata.normalize('NFC', content.replace('\r\n', '\n').replace('\r', '\n')).strip()
    require(0 < len(content) <= 4000, 'chat_content_invalid')
    return content


def create_conversation():
    uid = identity()
    with transaction() as session:
        row = Conversation(user_id=uid)
        session.add(row)
        session.flush()
        return snapshot(row)


def get_conversation(cid):
    uid = identity()
    with transaction() as session:
        return snapshot(owned(session, uid, cid))


def list_conversations(*, offset=0, limit=20):
    uid = identity()
    require(type(offset) is int and 0 <= offset <= 10000 and type(limit) is int and 1 <= limit <= 100,
            'chat_page_invalid')
    with transaction() as session:
        rows = session.scalars(sa.select(Conversation).where(Conversation.user_id == uid).order_by(
            Conversation.updated_at.desc(), Conversation.id.desc()).offset(offset).limit(limit))
        return [snapshot(row) for row in rows]


def get_request(cid, rid):
    uid = identity()
    request_id(rid)
    with transaction() as session:
        owned(session, uid, cid)
        row = session.scalar(sa.select(ChatTurn).where(
            ChatTurn.conversation_id == cid, ChatTurn.client_request_id == rid))
        return snapshot(row) if row else None


def begin_turn(cid, rid, content):
    uid = identity()
    request_id(rid)
    content = normalize(content)
    check_message_budget([{'role': 'user', 'content': content}])
    digest = hashlib.sha256(content.encode('utf-8')).hexdigest()
    with transaction() as session:
        # Conditional UPDATE is the first statement: acquire the write/row lock
        # before reading request state (also avoids stale MySQL snapshot races).
        claimed = session.execute(sa.update(Conversation).where(
            Conversation.id == cid, Conversation.user_id == uid,
            Conversation.active_request_id.is_(None), Conversation.archived_at.is_(None)
        ).values(active_request_id=rid, active_started_at=chat_utcnow()))
        convo = owned(session, uid, cid)
        existing = session.scalar(sa.select(ChatTurn).where(
            ChatTurn.conversation_id == cid, ChatTurn.client_request_id == rid))
        if existing:
            require(existing.request_digest == digest, 'chat_request_conflict')
            if claimed.rowcount == 1:
                convo.active_request_id = None
                convo.active_started_at = None
            return {'created': False, 'turn': snapshot(existing)}
        require(claimed.rowcount == 1, 'chat_busy')
        row = ChatTurn(conversation_id=cid, turn_no=convo.next_turn_no,
                       client_request_id=rid, request_digest=digest)
        convo.next_turn_no += 1
        convo.updated_at = chat_utcnow()
        session.add(row)
        session.flush()
        session.add(ChatMessage(turn_id=row.id, position=0, role='user', content=content))
        return {'created': True, 'turn': snapshot(row)}


def check_message_budget(messages):
    """Count the persisted protocol, including nullable fields and position.

    Database-generated IDs, timestamps and the foreign key are storage metadata,
    not model message fields. Keep the existing JSON character-count convention.
    """
    require(len(messages) <= 32, 'chat_protocol_invalid')
    protocol = [dict({key: message.get(key) for key in (
        'role', 'content', 'tool_calls', 'tool_call_id', 'tool_name')}, position=position)
        for position, message in enumerate(messages)]
    try:
        size = len(json.dumps(protocol, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError):
        raise ConversationError('chat_protocol_invalid') from None
    require(size <= 48000, 'chat_protocol_invalid')


def validated_messages(messages, completed):
    # Position zero belongs to the user message already saved by begin_turn().
    require(type(messages) is list and len(messages) <= 31, 'chat_protocol_invalid')
    try:
        json.dumps(messages, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        raise ConversationError('chat_protocol_invalid') from None
    pending, seen, result = {}, set(), []
    for source in messages:
        require(type(source) is dict and set(source) <= {
            'role', 'content', 'tool_calls', 'tool_call_id', 'tool_name'}, 'chat_protocol_invalid')
        msg = deepcopy(source)
        role, content = msg.get('role'), msg.get('content')
        require(content is None or type(content) is str, 'chat_protocol_invalid')
        if role == 'assistant':
            require(not pending and not msg.get('tool_call_id') and not msg.get('tool_name'),
                    'chat_protocol_invalid')
            calls = msg.get('tool_calls')
            try:
                ChatMessage(role=role, tool_calls=calls)
                for call in calls or []:
                    cid, function = call['id'], call['function']
                    require(type(cid) is str and 1 <= len(cid) <= 100
                            and call['type'] == 'function', 'chat_protocol_invalid')
                    require(type(function['name']) is str and 1 <= len(function['name']) <= 64,
                            'chat_protocol_invalid')
                    require(cid not in seen, 'chat_protocol_invalid')
                    validate(function['name'], json.loads(function['arguments']))
                    seen.add(cid)
                    pending[cid] = function['name']
            except (ValueError, TypeError, KeyError, AgentError):
                raise ConversationError('chat_protocol_invalid') from None
            require(bool(calls) or bool(content and content.strip()), 'chat_protocol_invalid')
        elif role == 'tool':
            cid = msg.get('tool_call_id')
            name = msg.get('tool_name')
            require(type(cid) is str and 1 <= len(cid) <= 100 and cid in pending
                    and type(name) is str and 1 <= len(name) <= 64 and name == pending[cid]
                    and content is not None and not msg.get('tool_calls'), 'chat_protocol_invalid')
            del pending[cid]
        else:
            raise ConversationError('chat_protocol_invalid')
        result.append(msg)
    if completed:
        require(bool(result) and not pending and result[-1]['role'] == 'assistant'
                and not result[-1].get('tool_calls'), 'chat_protocol_incomplete')
    return result


def finish_turn(cid, rid, *, status, messages, error_code=None):
    uid = identity()
    request_id(rid)
    require(status in ('completed', 'failed', 'interrupted'), 'chat_transition_invalid')
    require(error_code in (None, 'chat_execution_failed', 'chat_execution_interrupted'), 'chat_error_invalid')
    require(status != 'completed' or error_code is None, 'chat_error_invalid')
    messages = validated_messages(messages, status == 'completed')
    with transaction() as session:
        released = session.execute(sa.update(Conversation).where(
            Conversation.id == cid, Conversation.user_id == uid, Conversation.active_request_id == rid
        ).values(active_request_id=None, active_started_at=None, updated_at=chat_utcnow()))
        owned(session, uid, cid)
        require(released.rowcount == 1, 'chat_ownership_lost')
        row = session.scalar(sa.select(ChatTurn).where(
            ChatTurn.conversation_id == cid, ChatTurn.client_request_id == rid))
        require(row is not None and row.status == 'pending', 'chat_transition_invalid')
        user_message = session.scalar(sa.select(ChatMessage).where(
            ChatMessage.turn_id == row.id, ChatMessage.position == 0))
        require(user_message is not None and user_message.role == 'user', 'chat_protocol_invalid')
        check_message_budget([{'role': 'user', 'content': user_message.content}] + messages)
        for position, message in enumerate(messages, 1):
            session.add(ChatMessage(turn_id=row.id, position=position, **message))
        row.status, row.error_code, row.finished_at = status, error_code, chat_utcnow()
        session.flush()
        return snapshot(row)


def history(cid, *, completed_only=False, offset=0, limit=20):
    uid = identity()
    require(type(completed_only) is bool and type(offset) is int and 0 <= offset <= 10000
            and type(limit) is int and 1 <= limit <= 100, 'chat_page_invalid')
    with transaction() as session:
        owned(session, uid, cid)
        query = sa.select(ChatTurn).where(ChatTurn.conversation_id == cid)
        if completed_only:
            query = query.where(ChatTurn.status == 'completed')
        turns = session.scalars(query.order_by(ChatTurn.turn_no).offset(offset).limit(limit)).all()
        result = []
        for turn in turns:
            item = snapshot(turn)
            item['messages'] = [snapshot(m) for m in session.scalars(sa.select(ChatMessage).where(
                ChatMessage.turn_id == turn.id).order_by(ChatMessage.position))]
            result.append(item)
        return result


def recent_completed(cid, *, limit=7):
    """Read a bounded tail, in chronological order, with owner verification."""
    uid = identity()
    require(type(limit) is int and 1 <= limit <= 7, 'chat_page_invalid')
    with transaction() as session:
        owned(session, uid, cid)
        turns = session.scalars(sa.select(ChatTurn).where(
            ChatTurn.conversation_id == cid, ChatTurn.status == 'completed'
        ).order_by(ChatTurn.turn_no.desc()).limit(limit)).all()
        result = []
        for turn in reversed(turns):
            item = snapshot(turn)
            item['messages'] = [snapshot(m) for m in session.scalars(sa.select(ChatMessage).where(
                ChatMessage.turn_id == turn.id).order_by(ChatMessage.position))]
            result.append(item)
        return result
