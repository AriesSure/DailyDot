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
from app import db, login
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
        return f'Habit(id={self.id}, event={self.event}, date={self.date}, time = {self.time}, note={self.note}, user_id={self.user_id})'


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
        from app import db
        return db.session.query(Record).filter_by(habit_id=self.id).count() # Use database queries to obtain accurate record counts



class Record(db.Model):
    __tablename__ = 'records'
    __table_args__ = (
        sa.UniqueConstraint('checkin_date', 'habit_id', name='uix_date_habit'),  # You cannot check in for the same habit more than once on the same day
    )
    id: so.Mapped[int] = so.mapped_column(primary_key=True)
    checkin_date: so.Mapped[date] = so.mapped_column(sa.Date, default=date.today, nullable=False)
    checkin_time: so.Mapped[time] = so.mapped_column(sa.Time, nullable=False, default=datetime.utcnow().time())
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
