"""Statistics business logic."""

from datetime import date, timedelta

from app.extensions import db
from app.models import Habit, Record, Todo


def get_habit_stats(user_id):
    """Return list of ``{name, total_checkins, icon}`` sorted by check-in count desc."""
    habits = db.session.query(Habit).filter_by(user_id=user_id).all()
    stats = []
    for habit in habits:
        stats.append({
            'name': habit.habit_name,
            'total_checkins': habit.completed_count(),
            'icon': habit.icon,
        })
    stats.sort(key=lambda x: x['total_checkins'], reverse=True)
    return stats


def get_daily_checkin_data(user_id, days=14):
    """Return list of ``{date, count}`` for the last *days* days."""
    start = date.today() - timedelta(days=days)
    records = db.session.query(Record).join(Habit).filter(
        Habit.user_id == user_id,
        Record.checkin_date >= start,
    ).all()

    data = []
    for i in range(days - 1, -1, -1):
        day = date.today() - timedelta(days=i)
        count = sum(1 for r in records if r.checkin_date == day)
        data.append({'date': day.strftime('%m/%d'), 'count': count})
    return data


def get_todo_stats(user_id):
    """Return ``(total, completed, rate)``."""
    todos = db.session.query(Todo).filter_by(user_id=user_id).all()
    total = len(todos)
    completed = sum(1 for t in todos if t.completed)
    rate = (completed / total * 100) if total else 0
    return total, completed, rate


def get_annual_data(habit_id, year):
    """Build monthly calendar data for a habit in a given year.

    Returns a list of monthly dicts:
    ``{month_name, year, first_day_of_week, days_in_month, daily_status}``
    """
    start_date = date(year, 1, 1)
    end_date = date(year, 12, 31)

    records = db.session.query(Record).filter(
        Record.habit_id == habit_id,
        Record.checkin_date >= start_date,
        Record.checkin_date <= end_date,
    ).all()

    checkin_dates = {r.checkin_date.strftime('%Y-%m-%d') for r in records}

    monthly_data = []
    for month in range(1, 13):
        month_start = date(year, month, 1)
        month_end = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
        days_in_month = (month_end - month_start).days + 1

        daily_status = []
        for day in range(1, days_in_month + 1):
            d = date(year, month, day)
            daily_status.append(d.strftime('%Y-%m-%d') in checkin_dates)

        monthly_data.append({
            'month_name': month_start.strftime('%B'),
            'year': year,
            'first_day_of_week': month_start.weekday(),
            'days_in_month': days_in_month,
            'daily_status': daily_status,
        })
    return monthly_data
