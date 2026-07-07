"""Stats blueprint — statistics dashboard and annual view."""

from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import current_user, login_required
from datetime import date, timedelta

from app.extensions import db
from app.models import Habit, Record, Todo

stats_bp = Blueprint('stats', __name__, url_prefix='/stats')


@stats_bp.route('/')
@login_required
def statistics():
    try:
        habits = db.session.query(Habit).filter_by(user_id=current_user.id).all()
        thirty_days_ago = date.today() - timedelta(days=30)
        recent_records = db.session.query(Record).join(Habit).filter(
            Habit.user_id == current_user.id,
            Record.checkin_date >= thirty_days_ago
        ).all()

        todos = db.session.query(Todo).filter_by(user_id=current_user.id).all()
        completed_todos = [todo for todo in todos if todo.completed]

        habit_stats = []
        for habit in habits:
            total_checkins = habit.completed_count()
            habit_stats.append({
                'name': habit.habit_name,
                'total_checkins': total_checkins,
                'icon': habit.icon
            })
        habit_stats.sort(key=lambda x: x['total_checkins'], reverse=True)

        daily_data = []
        for i in range(14):
            day = date.today() - timedelta(days=i)
            day_records = [r for r in recent_records if r.checkin_date == day]
            daily_data.append({
                'date': day.strftime('%m/%d'),
                'count': len(day_records)
            })
        daily_data.reverse()

        todo_completion_rate = len(completed_todos) / len(todos) * 100 if todos else 0

        return render_template('statistics.html',
                               title='Statistics',
                               habit_stats=habit_stats,
                               daily_data=daily_data,
                               todo_completion_rate=todo_completion_rate,
                               total_todos=len(todos),
                               completed_todos=len(completed_todos))
    except Exception as e:
        db.session.rollback()
        flash(f'Failed to load statistics: {str(e)}', 'danger')
        return render_template('statistics.html', title='Statistics',
                               habit_stats=[], daily_data=[], todo_completion_rate=0,
                               total_todos=0, completed_todos=0)


@stats_bp.route('/annual/<int:habit_id>')
@login_required
def annual_statistics(habit_id):
    try:
        habit = db.session.query(Habit).filter_by(id=habit_id, user_id=current_user.id).first()
        if not habit:
            flash('Habit does not exist', 'danger')
            return redirect(url_for('habits.my_habits'))

        year = request.args.get('year', date.today().year, type=int)
        start_date = date(year, 1, 1)
        end_date = date(year, 12, 31)

        records = db.session.query(Record).filter(
            Record.habit_id == habit_id,
            Record.checkin_date >= start_date,
            Record.checkin_date <= end_date
        ).all()

        checkin_dates = {record.checkin_date.strftime('%Y-%m-%d') for record in records}

        monthly_data = []
        for month in range(1, 13):
            month_start = date(year, month, 1)
            month_end = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
            first_day_of_week = month_start.weekday()
            days_in_month = (month_end - month_start).days + 1
            daily_status = []
            for day in range(1, days_in_month + 1):
                checkin_date = date(year, month, day)
                daily_status.append(checkin_date.strftime('%Y-%m-%d') in checkin_dates)

            monthly_data.append({
                'month_name': month_start.strftime('%B'),
                'year': year,
                'first_day_of_week': first_day_of_week,
                'days_in_month': days_in_month,
                'daily_status': daily_status
            })

        return render_template('annual.html',
                               title=f'{habit.habit_name} - Annual Statistics',
                               habit=habit,
                               monthly_data=monthly_data,
                               current_year=year)
    except Exception as e:
        db.session.rollback()
        flash(f'Failed to load annual statistics: {str(e)}', 'danger')
        return redirect(url_for('habits.my_habits'))
