"""Habit business logic."""

from app.extensions import db
from app.models import Habit


def create_habit(habit_name, icon, frequency, time_period, note, user_id):
    """Create a new habit for *user_id*.  Raises ``ValueError`` on duplicate name."""
    existing = db.session.query(Habit).filter_by(
        habit_name=habit_name, user_id=user_id
    ).first()
    if existing:
        raise ValueError('Habit name already exists.')

    habit = Habit(
        habit_name=habit_name,
        icon=icon,
        frequency=frequency,
        time_period=time_period,
        note=note or '',
        user_id=user_id,
    )
    db.session.add(habit)
    db.session.commit()
    return habit


def update_habit(habit, habit_name, icon, frequency, time_period, note, user_id):
    """Update an existing habit.  Raises ``ValueError`` on name collision."""
    duplicate = db.session.query(Habit).filter(
        Habit.habit_name == habit_name,
        Habit.user_id == user_id,
        Habit.id != habit.id,
    ).first()
    if duplicate:
        raise ValueError('Habit name already exists.')

    habit.habit_name = habit_name
    habit.icon = icon
    habit.frequency = frequency
    habit.time_period = time_period
    habit.note = note
    db.session.commit()


def delete_habit(habit):
    """Delete a habit and its check-in records."""
    from app.models import Record
    db.session.query(Record).filter_by(habit_id=habit.id).delete()
    db.session.delete(habit)
    db.session.commit()


def get_habit_or_none(habit_id, user_id):
    """Return habit if it belongs to *user_id*, else ``None``."""
    return db.session.query(Habit).filter_by(id=habit_id, user_id=user_id).first()
