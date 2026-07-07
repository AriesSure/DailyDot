"""Main blueprint — home page (the only route at /)."""

from flask import Blueprint, render_template, flash, request
from flask_login import current_user, login_required
from datetime import date, timedelta
from sqlalchemy.sql import and_
import sqlalchemy as sa

from app.extensions import db
from app.models import Habit, Record, Todo
from app.utils.query_helpers import get_time_period_order

main_bp = Blueprint('main', __name__)


@main_bp.route("/")
def home():
    if current_user.is_authenticated:
        try:
            today = date.today()
            time_period_order = get_time_period_order()

            unchecked_habits = db.session.query(Habit).outerjoin(Record, and_(
                Habit.id == Record.habit_id,
                Record.checkin_date == today
            )).filter(Habit.user_id == current_user.id,
                      Record.id.is_(None)).order_by(time_period_order).all()

            checked_habits = db.session.query(Habit).join(Record, and_(
                Habit.id == Record.habit_id,
                Record.checkin_date == today
            )).filter(Habit.user_id == current_user.id).order_by(time_period_order).all()

            today_str = today.strftime('%d/%m/%Y')
            today_todos = db.session.query(Todo).filter(
                Todo.user_id == current_user.id,
                Todo.date == today_str
            ).order_by(Todo.time).all()

            return render_template('home.html', title='Home',
                                   unchecked_habits=unchecked_habits,
                                   checked_habits=checked_habits,
                                   today_todos=today_todos)
        except Exception as e:
            db.session.rollback()
            flash(f'Failed to retrieve unchecked habits:{str(e)}', 'danger')
            return render_template('home.html', title='Home',
                                   unchecked_habits=[], checked_habits=[], today_todos=[])
    else:
        return render_template('home.html', title='Welcome')
