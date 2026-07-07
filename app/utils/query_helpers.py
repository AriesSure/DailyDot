"""Reusable database query helpers to avoid code duplication."""

from flask import jsonify
from app.extensions import db
from app.models import Habit, Record
import sqlalchemy as sa


def get_time_period_order(model=Habit):
    """Return a ``sa.case()`` expression for sorting by ``time_period``."""
    return sa.case(
        (model.time_period == '', 0),
        (model.time_period == 'Any time', 1),
        (model.time_period == 'After waking up', 2),
        (model.time_period == 'Morning', 3),
        (model.time_period == 'Noon', 4),
        (model.time_period == 'Afternoon', 5),
        (model.time_period == 'Evening', 6),
        (model.time_period == 'Before bedtime', 7),
        else_=8,
    )


def get_habit_or_json_error(habit_id, user_id):
    """Look up a habit by id + user_id.  Return ``(habit, None)`` on success,
    or ``(None, json_response)`` on failure."""
    habit = db.session.query(Habit).filter_by(id=habit_id, user_id=user_id).first()
    if not habit:
        return None, jsonify({'success': False, 'message': 'Habit does not exist'})
    return habit, None


def get_record_or_json_error(log_id, user_id):
    """Look up a Record (joined through Habit) by id + ownership check."""
    record = db.session.query(Record).join(Habit).filter(
        Record.id == log_id, Habit.user_id == user_id
    ).first()
    if not record:
        return None, jsonify({'success': False, 'message': 'Log does not exist or no permission'})
    return record, None
