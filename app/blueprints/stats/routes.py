"""Stats blueprint — statistics dashboard and annual view."""

from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import current_user, login_required
from datetime import date

from app.extensions import db
from app.services.habit_service import get_habit_or_none
from app.services.statistics_service import (
    get_habit_stats,
    get_daily_checkin_data,
    get_todo_stats,
    get_annual_data,
)

stats_bp = Blueprint('stats', __name__, url_prefix='/stats')


@stats_bp.route('/')
@login_required
def statistics():
    try:
        return render_template(
            'statistics.html',
            title='Statistics',
            habit_stats=get_habit_stats(current_user.id),
            daily_data=get_daily_checkin_data(current_user.id),
            todo_completion_rate=get_todo_stats(current_user.id)[2],
            total_todos=get_todo_stats(current_user.id)[0],
            completed_todos=get_todo_stats(current_user.id)[1],
        )
    except Exception as e:
        db.session.rollback()
        flash(f'Failed to load statistics: {str(e)}', 'danger')
        return render_template('statistics.html', title='Statistics',
                               habit_stats=[], daily_data=[], todo_completion_rate=0,
                               total_todos=0, completed_todos=0)


@stats_bp.route('/annual/<int:habit_id>')
@login_required
def annual_statistics(habit_id):
    habit = get_habit_or_none(habit_id, current_user.id)
    if not habit:
        flash('Habit does not exist', 'danger')
        return redirect(url_for('habits.my_habits'))

    try:
        year = request.args.get('year', date.today().year, type=int)
        monthly_data = get_annual_data(habit_id, year)
        return render_template(
            'annual.html',
            title=f'{habit.habit_name} - Annual Statistics',
            habit=habit,
            monthly_data=monthly_data,
            current_year=year,
        )
    except Exception as e:
        db.session.rollback()
        flash(f'Failed to load annual statistics: {str(e)}', 'danger')
        return redirect(url_for('habits.my_habits'))
