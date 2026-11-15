import json
from uuid import uuid4
from datetime import datetime
from datetime import date, time
import datetime as dt
from typing import Optional
import sqlalchemy as sa
import sqlalchemy.orm as so
from flask_login import UserMixin
from sqlalchemy import ForeignKey
# Use the official ORM mapped_column via sqlalchemy.orm (we will reference so.mapped_column below)
from sqlalchemy.orm import relationship
from werkzeug.security import generate_password_hash, check_password_hash
from app.extensions import db, login
from dataclasses import dataclass


@dataclass
class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id: so.Mapped[int] = so.mapped_column(primary_key=True)
    username: so.Mapped[str] = so.mapped_column(sa.String(64), index=True, unique=True)
    email: so.Mapped[str] = so.mapped_column(sa.String(120), index=True, unique=True)
    password_hash: so.Mapped[Optional[str]] = so.mapped_column(sa.String(256))
    habits: so.Mapped[list['Habit']] = relationship(back_populates='user', cascade='all, delete-orphan')
    todos: so.Mapped[list['Todo']] = relationship(back_populates='user', cascade='all, delete-orphan')
    # cards: so.Mapped[list['Card']] = relationship(back_populates='user', cascade='all, delete-orphan')


    def __repr__(self):
        pwh= 'None' if not self.password_hash else f'...{self.password_hash[-5:]}'
        return f'User(id={self.id}, username={self.username}, email={self.email}, pwh={pwh})'

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

@login.user_loader
def load_user(id):
    return db.session.get(User, int(id))


class Todo(db.Model):
    __tablename__ = 'todos'

    id: so.Mapped[int] = so.mapped_column(primary_key=True)
    event: so.Mapped[str] = so.mapped_column(sa.String(64), index=True)
    date: so.Mapped[str] = so.mapped_column(sa.String(16))
    time: so.Mapped[str] = so.mapped_column(sa.String(16))
    note: so.Mapped[str] = so.mapped_column(sa.String(1024), nullable=True)
    completed: so.Mapped[bool] = so.mapped_column(sa.Boolean, default=False)
    created_at: so.Mapped[datetime] = so.mapped_column(sa.DateTime, default=datetime.utcnow)
    user_id: so.Mapped[int]  = so.mapped_column(ForeignKey('users.id'), index=True)
    user: so.Mapped['User'] = relationship(back_populates='todos')

    def __repr__(self):
        return f'Todo(id={self.id}, event={self.event}, date={self.date}, time = {self.time}, note={self.note}, user_id={self.user_id})'


class Habit(db.Model):
    __tablename__ = 'habits'

    id: so.Mapped[int] = so.mapped_column(primary_key=True)
    habit_name: so.Mapped[str] = so.mapped_column(sa.String(64), index=True)
    icon: so.Mapped[str] = so.mapped_column(sa.String(32), nullable=False)
    frequency: so.Mapped[str] = so.mapped_column(sa.String(64), nullable=True)
    time_period: so.Mapped[str] = so.mapped_column(sa.String(64), nullable=True)
    note: so.Mapped[str] = so.mapped_column(sa.String(1024), nullable=True)
    user_id: so.Mapped[int] = so.mapped_column(ForeignKey('users.id'), index=True)
    user: so.Mapped['User'] = relationship(back_populates='habits')
    records: so.Mapped['Record'] = relationship(back_populates='habit', cascade='all, delete-orphan')

    def __repr__(self):
        return f'Habit(id={self.id}, name={self.habit_name}, frequency={self.frequency}, time_period = {self.time_period}, note={self.note}, user_id={self.user_id})'

    def completed_count(self):
        from app.extensions import db
        return db.session.query(Record).filter_by(habit_id=self.id).count()



class Record(db.Model):
    __tablename__ = 'records'
    __table_args__ = (
        sa.UniqueConstraint('checkin_date', 'habit_id', name='uix_date_habit'),  # You cannot check in for the same habit more than once on the same day
    )
    id: so.Mapped[int] = so.mapped_column(primary_key=True)
    checkin_date: so.Mapped[date] = so.mapped_column(sa.Date, default=date.today, nullable=False)
    checkin_time: so.Mapped[Optional[time]] = so.mapped_column(sa.Time, nullable=True, default=lambda: datetime.utcnow().time())
    habit_id: so.Mapped[int] = so.mapped_column(ForeignKey('habits.id'), index=True)
    habit: so.Mapped['Habit'] = relationship(back_populates='records')
    note: so.Mapped[str] = so.mapped_column(sa.String(1024), nullable=True)

    def __repr__(self):
        return f'Record(habit_id={self.habit_id}, checkin_date={self.checkin_date}, checkin_time={self.checkin_time})'



class Card(db.Model):
    __tablename__ = 'cards'
    __table_args__ = (
        sa.UniqueConstraint('date', 'user_id', name='uix_date_user'),
    )
    id: so.Mapped[int] = so.mapped_column(primary_key=True)
    date: so.Mapped[dt.date] = so.mapped_column(sa.Date, default=dt.date.today, nullable=False)
    user_id: so.Mapped[int] = so.mapped_column(ForeignKey('users.id'), index=True)

    image_url: so.Mapped[Optional[str]] = so.mapped_column(sa.String(255), nullable=True)
    quote: so.Mapped[Optional[str]] = so.mapped_column(sa.String(255), nullable=True)
    weekday: so.Mapped[Optional[str]] = so.mapped_column(sa.String(10), nullable=True)
    streak: so.Mapped[int] = so.mapped_column(sa.Integer, default=0, nullable=False)
    habits_completed: so.Mapped[int] = so.mapped_column(sa.Integer, default=0, nullable=False)

    def __repr__(self):
        return (f'Card(id={self.id}, date={self.date}, user_id={self.user_id}, '
                f'weekday="{self.weekday}", streak={self.streak}, '
                f'habits_completed={self.habits_completed})')


def chat_utcnow():
    """UTC in a portable, timezone-naive SQL DATETIME representation."""
    return datetime.now(dt.timezone.utc).replace(tzinfo=None)


class AgentAdmissionGate(db.Model):
    __tablename__ = 'agent_admission_gate'
    __table_args__ = (
        sa.PrimaryKeyConstraint('gate_key', name='pk_agent_admission_gate'),
        sa.CheckConstraint("gate_key = 'global' AND length(gate_key) = 6", name='ck_agent_gate_key'),
        sa.CheckConstraint('lock_version >= 0', name='ck_agent_gate_version'),
        {'mysql_engine': 'InnoDB'},
    )
    gate_key: so.Mapped[str] = so.mapped_column(
        sa.String(16, collation='utf8mb4_bin').with_variant(sa.String(16, collation='BINARY'), 'sqlite'))
    lock_version: so.Mapped[int] = so.mapped_column(sa.BigInteger, nullable=False, default=0)


class ChatUTCDateTime(sa.TypeDecorator):
    impl = sa.DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None and value.tzinfo is not None:
            return value.astimezone(dt.timezone.utc).replace(tzinfo=None)
        return value  # Explicit naive values are interpreted as UTC.

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return (value.replace(tzinfo=dt.timezone.utc) if value.tzinfo is None
                else value.astimezone(dt.timezone.utc))


class Conversation(db.Model):
    __tablename__ = 'conversations'
    __table_args__ = (
        sa.PrimaryKeyConstraint('id', name='pk_conversations'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='fk_conversations_user', ondelete='RESTRICT'),
        sa.Index('ix_conversations_owner_updated', 'user_id', 'updated_at', 'id'),
        sa.CheckConstraint('next_turn_no >= 1', name='ck_conversations_next_turn'),
    )
    id: so.Mapped[str] = so.mapped_column(sa.String(32), default=lambda: uuid4().hex)
    user_id: so.Mapped[int] = so.mapped_column(sa.Integer, nullable=False)
    title: so.Mapped[str] = so.mapped_column(sa.String(120), default='新对话', nullable=False)
    created_at: so.Mapped[datetime] = so.mapped_column(ChatUTCDateTime(), default=chat_utcnow, nullable=False)
    updated_at: so.Mapped[datetime] = so.mapped_column(ChatUTCDateTime(), default=chat_utcnow, nullable=False)
    next_turn_no: so.Mapped[int] = so.mapped_column(sa.Integer, default=1, nullable=False)
    active_request_id: so.Mapped[Optional[str]] = so.mapped_column(sa.String(32), nullable=True)
    active_started_at: so.Mapped[Optional[datetime]] = so.mapped_column(ChatUTCDateTime(), nullable=True)
    archived_at: so.Mapped[Optional[datetime]] = so.mapped_column(ChatUTCDateTime(), nullable=True)
    turns: so.Mapped[list['ChatTurn']] = relationship(
        back_populates='conversation', cascade='all, delete-orphan', passive_deletes=True)


class ChatTurn(db.Model):
    __tablename__ = 'chat_turns'
    __table_args__ = (
        sa.PrimaryKeyConstraint('id', name='pk_chat_turns'),
        sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'],
                                name='fk_chat_turns_conversation', ondelete='CASCADE'),
        sa.UniqueConstraint('conversation_id', 'turn_no', name='uq_chat_turns_number'),
        sa.UniqueConstraint('conversation_id', 'client_request_id', name='uq_chat_turns_request'),
        sa.Index('ix_chat_turns_conversation', 'conversation_id'),
        sa.CheckConstraint(
            "status IN ('pending','completed','failed','interrupted') AND length(status) = length(trim(status))",
            name='ck_chat_turns_status'),
        sa.CheckConstraint('turn_no >= 1', name='ck_chat_turns_number'),
    )
    id: so.Mapped[int] = so.mapped_column(primary_key=True)
    conversation_id: so.Mapped[str] = so.mapped_column(sa.String(32), nullable=False)
    turn_no: so.Mapped[int] = so.mapped_column(sa.Integer, nullable=False)
    client_request_id: so.Mapped[str] = so.mapped_column(sa.String(32), nullable=False)
    request_digest: so.Mapped[str] = so.mapped_column(sa.String(64), nullable=False)
    status: so.Mapped[str] = so.mapped_column(
        sa.String(16, collation='utf8mb4_bin').with_variant(sa.String(16, collation='BINARY'), 'sqlite'),
        default='pending', nullable=False)
    created_at: so.Mapped[datetime] = so.mapped_column(ChatUTCDateTime(), default=chat_utcnow, nullable=False)
    finished_at: so.Mapped[Optional[datetime]] = so.mapped_column(ChatUTCDateTime(), nullable=True)
    error_code: so.Mapped[Optional[str]] = so.mapped_column(sa.String(64), nullable=True)
    conversation: so.Mapped['Conversation'] = relationship(back_populates='turns')
    messages: so.Mapped[list['ChatMessage']] = relationship(
        back_populates='turn', cascade='all, delete-orphan', passive_deletes=True)

    @so.validates('client_request_id', 'request_digest')
    def validate_hex(self, key, value):
        size = 32 if key == 'client_request_id' else 64
        if not isinstance(value, str) or len(value) != size or any(c not in '0123456789abcdef' for c in value):
            raise ValueError('chat_invalid_hex_identifier')
        return value


class ChatMessage(db.Model):
    __tablename__ = 'chat_messages'
    __table_args__ = (
        sa.PrimaryKeyConstraint('id', name='pk_chat_messages'),
        sa.ForeignKeyConstraint(['turn_id'], ['chat_turns.id'], name='fk_chat_messages_turn', ondelete='CASCADE'),
        sa.UniqueConstraint('turn_id', 'position', name='uq_chat_messages_position'),
        sa.Index('ix_chat_messages_turn', 'turn_id'),
        sa.CheckConstraint(
            "role IN ('user','assistant','tool') AND length(role) = length(trim(role))",
            name='ck_chat_messages_role'),
        sa.CheckConstraint('position >= 0', name='ck_chat_messages_position'),
    )
    id: so.Mapped[int] = so.mapped_column(primary_key=True)
    turn_id: so.Mapped[int] = so.mapped_column(sa.Integer, nullable=False)
    position: so.Mapped[int] = so.mapped_column(sa.Integer, nullable=False)
    role: so.Mapped[str] = so.mapped_column(
        sa.String(16, collation='utf8mb4_bin').with_variant(sa.String(16, collation='BINARY'), 'sqlite'),
        nullable=False)
    content: so.Mapped[Optional[str]] = so.mapped_column(sa.Text, nullable=True)
    tool_calls: so.Mapped[Optional[list]] = so.mapped_column(sa.JSON(none_as_null=True), nullable=True)
    tool_call_id: so.Mapped[Optional[str]] = so.mapped_column(sa.String(100), nullable=True)
    tool_name: so.Mapped[Optional[str]] = so.mapped_column(sa.String(64), nullable=True)
    created_at: so.Mapped[datetime] = so.mapped_column(ChatUTCDateTime(), default=chat_utcnow, nullable=False)
    turn: so.Mapped['ChatTurn'] = relationship(back_populates='messages')

    @so.validates('tool_calls')
    def validate_tool_calls(self, key, value):
        if value is None:
            return None
        if type(value) is not list:
            raise ValueError('chat_invalid_tool_calls')
        seen = set()
        for call in value:
            if type(call) is not dict or set(call) != {'id', 'type', 'function'}:
                raise ValueError('chat_invalid_tool_calls')
            cid, function = call['id'], call['function']
            if (type(cid) is not str or not 1 <= len(cid) <= 100 or cid in seen
                    or call['type'] != 'function' or type(function) is not dict
                    or set(function) != {'name', 'arguments'}):
                raise ValueError('chat_invalid_tool_calls')
            if function['name'] not in ('list_user_habits', 'get_checkin_summary'):
                raise ValueError('chat_invalid_tool_calls')
            try:
                if type(function['arguments']) is not str or type(json.loads(function['arguments'])) is not dict:
                    raise ValueError()
            except (TypeError, ValueError):
                raise ValueError('chat_invalid_tool_calls') from None
            seen.add(cid)
        return value
