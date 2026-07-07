"""Record (check-in) business logic."""

from datetime import date, datetime

from app.extensions import db
from app.models import Record, Habit


def check_in(habit_id, user_id, checkin_date=None, checkin_time=None, note=''):
    """Record a check-in for *habit_id*.  Raises ``ValueError`` if already checked in."""
    if checkin_date is None:
        checkin_date = date.today()
    if checkin_time is None:
        checkin_time = datetime.now().time()

    # Ownership check
    habit = db.session.query(Habit).filter_by(id=habit_id, user_id=user_id).first()
    if not habit:
        raise ValueError('Habit does not exist')

    existing = db.session.query(Record).filter_by(
        habit_id=habit_id, checkin_date=checkin_date
    ).first()
    if existing:
        raise ValueError('Already checked in for this day')

    record = Record(
        habit_id=habit_id,
        checkin_date=checkin_date,
        checkin_time=checkin_time,
        note=note,
    )
    db.session.add(record)
    db.session.commit()
    return record


def uncheck(habit_id, user_id, checkin_date=None):
    """Remove a check-in record."""
    if checkin_date is None:
        checkin_date = date.today()

    habit = db.session.query(Habit).filter_by(id=habit_id, user_id=user_id).first()
    if not habit:
        raise ValueError('Habit does not exist')

    record = db.session.query(Record).filter_by(
        habit_id=habit_id, checkin_date=checkin_date
    ).first()
    if not record:
        raise ValueError('No check-in record for this day')

    db.session.delete(record)
    db.session.commit()


def get_records_for_habit(habit_id, with_notes_only=False):
    """Return records for a habit, optionally filtering to those with notes."""
    q = db.session.query(Record).filter(Record.habit_id == habit_id)
    if with_notes_only:
        q = q.filter(Record.note.isnot(None), Record.note != '')
    return q.order_by(Record.checkin_date.desc(), Record.checkin_time.desc()).all()


def get_all_checkin_dates(habit_id):
    """Return ISO-format date strings for all check-ins of a habit."""
    rows = db.session.query(Record.checkin_date).filter(
        Record.habit_id == habit_id
    ).all()
    return [r.checkin_date.isoformat() for r in rows]


def count_checkins(habit_id, since_date=None):
    """Count check-ins, optionally filtered by a start date."""
    q = db.session.query(Record).filter(Record.habit_id == habit_id)
    if since_date:
        q = q.filter(Record.checkin_date >= since_date)
    return q.count()
