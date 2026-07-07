"""Habits blueprint — CRUD, check-in / uncheck, logs."""

from flask import Blueprint, render_template, jsonify, redirect, url_for, flash, request
from flask_login import current_user, login_required
from datetime import date, datetime, timedelta

from app.extensions import db
from app.models import Habit
from app.forms import HabitForm, CheckinForm
from app.utils.constants import ICON_CHOICES
from app.utils.query_helpers import get_time_period_order, get_record_or_json_error
from app.services.habit_service import create_habit, update_habit, delete_habit, get_habit_or_none
from app.services.record_service import (
    check_in as record_check_in,
    uncheck as record_uncheck,
    get_records_for_habit,
    get_all_checkin_dates,
    count_checkins,
)

habits_bp = Blueprint('habits', __name__, url_prefix='/habits')


@habits_bp.route('/')
@login_required
def my_habits():
    return render_template(
        'my_habits.html',
        title='My Habits',
        habits=Habit.query.filter_by(user_id=current_user.id)
               .order_by(get_time_period_order()).all(),
    )


@habits_bp.route('/new', methods=['GET', 'POST'])
@login_required
def new_habit():
    if request.is_json:
        data = request.get_json()
        try:
            create_habit(
                habit_name=data.get('habit_name'),
                icon=data.get('icon'),
                frequency=data.get('frequency'),
                time_period=data.get('time_period'),
                note=data.get('note', ''),
                user_id=current_user.id,
            )
            return jsonify({'success': True, 'message': 'Habit created successfully!'})
        except ValueError as e:
            return jsonify({'success': False, 'message': str(e)})
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'message': f'Error creating habit: {str(e)}'})

    form = HabitForm()
    form.icon.choices = ICON_CHOICES
    if form.validate_on_submit():
        try:
            create_habit(
                habit_name=form.habit_name.data,
                icon=form.icon.data,
                frequency=form.frequency.data,
                time_period=form.time_period.data,
                note=form.note.data,
                user_id=current_user.id,
            )
            flash('Habit created successfully!', 'success')
            return redirect(url_for('main.home'))
        except ValueError as e:
            flash(str(e), 'danger')
        except Exception as e:
            db.session.rollback()
            flash(f'Error creating habit: {str(e)}', 'danger')
    return render_template('new_habit.html', title='New Habit', form=form)


@habits_bp.route('/view/<int:habit_id>')
@login_required
def habit_view(habit_id):
    habit = get_habit_or_none(habit_id, current_user.id)
    if not habit:
        flash('Habit does not exist', 'danger')
        return redirect(url_for('habits.my_habits'))

    today = date.today()
    current_month_start = date(today.year, today.month, 1)
    current_week_start = today - timedelta(days=today.weekday())

    return render_template(
        'habit_view.html',
        title=f'View {habit.habit_name}',
        habit=habit,
        current_month_count=count_checkins(habit_id, since_date=current_month_start),
        current_week_count=count_checkins(habit_id, since_date=current_week_start),
        recent_records=get_records_for_habit(habit_id, with_notes_only=True),
        checkin_dates=get_all_checkin_dates(habit_id),
    )


@habits_bp.route('/edit/<int:habit_id>', methods=['GET', 'POST'])
@login_required
def habit_edit(habit_id):
    habit = get_habit_or_none(habit_id, current_user.id)
    if not habit:
        flash('Habit does not exist', 'danger')
        return redirect(url_for('habits.my_habits'))

    form = HabitForm()
    form.icon.choices = ICON_CHOICES

    if request.method == 'GET':
        form.habit_name.data = habit.habit_name
        form.icon.data = habit.icon
        form.frequency.data = habit.frequency
        form.time_period.data = habit.time_period
        form.note.data = habit.note

    if form.validate_on_submit():
        try:
            update_habit(
                habit,
                habit_name=form.habit_name.data,
                icon=form.icon.data,
                frequency=form.frequency.data,
                time_period=form.time_period.data,
                note=form.note.data,
                user_id=current_user.id,
            )
            flash('Habit updated successfully!', 'success')
            return redirect(url_for('habits.habit_view', habit_id=habit.id))
        except ValueError as e:
            flash(str(e), 'danger')
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating habit: {str(e)}', 'danger')

    return render_template('habit_edit.html', title='Edit Habit', form=form, habit=habit)


@habits_bp.route('/delete/<int:habit_id>', methods=['POST'])
@login_required
def habit_delete(habit_id):
    habit = get_habit_or_none(habit_id, current_user.id)
    if not habit:
        return jsonify({'success': False, 'message': 'Habit does not exist'})
    try:
        delete_habit(habit)
        return jsonify({'success': True, 'message': 'Habit deleted successfully'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)})


@habits_bp.route('/check_in/<int:habit_id>', methods=['GET', 'POST'])
@login_required
def check_in(habit_id):
    habit = get_habit_or_none(habit_id, current_user.id)
    if not habit:
        if request.is_json:
            return jsonify({'success': False, 'message': 'Habit does not exist'})
        flash('Habit does not exist', 'danger')
        return redirect(url_for('main.home'))

    if request.is_json:
        data = request.get_json()
        try:
            time_str = data.get('time', '')
            time_obj = datetime.strptime(time_str, '%H:%M').time() if time_str else datetime.now().time()
            record_check_in(habit_id, current_user.id, checkin_time=time_obj, note=data.get('note', ''))
            return jsonify({'success': True, 'message': 'Checked in successfully'})
        except ValueError as e:
            return jsonify({'success': False, 'message': str(e)})
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'message': f'Error saving check-in: {str(e)}'})

    form = CheckinForm()
    if request.method == 'GET':
        form.time.data = datetime.now().time()
    if request.method == 'POST' and form.validate_on_submit():
        try:
            record_check_in(habit_id, current_user.id, checkin_time=form.time.data, note=form.note.data)
            flash('Checked in successfully', 'success')
        except ValueError as e:
            flash(str(e), 'warning')
        except Exception as e:
            db.session.rollback()
            flash(f'Error saving check-in: {str(e)}', 'danger')
        return redirect(url_for('main.home'))

    from app.models import Record
    record_exist = bool(db.session.query(Record).filter(
        Record.habit_id == habit_id, Record.checkin_date == date.today()
    ).first())
    return render_template('checkin.html', title='Check In', habit=habit, form=form,
                           record_exist=record_exist)


@habits_bp.route('/uncheck/<int:habit_id>', methods=['GET', 'POST'])
@login_required
def uncheck(habit_id):
    try:
        record_uncheck(habit_id, current_user.id)
        flash('Unchecked successfully', 'success')
    except ValueError as e:
        flash(str(e), 'warning')
    except Exception as e:
        db.session.rollback()
        flash(f'Error unchecking: {str(e)}', 'danger')
    return redirect(url_for('main.home'))


@habits_bp.route('/log_clear_note/<int:log_id>', methods=['POST'])
@login_required
def habit_log_clear_note(log_id):
    record, err = get_record_or_json_error(log_id, current_user.id)
    if err:
        return err
    try:
        record.note = ''
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)})


@habits_bp.route('/checkin_by_date/<int:habit_id>', methods=['POST'])
@login_required
def habit_checkin_by_date(habit_id):
    data = request.get_json()
    try:
        cd = datetime.strptime(data.get('date', ''), '%Y-%m-%d').date()
        ct = datetime.strptime(data.get('time', ''), '%H:%M').time() if data.get('time') else datetime.now().time()
        record_check_in(habit_id, current_user.id, checkin_date=cd, checkin_time=ct, note=data.get('note', ''))
        return jsonify({'success': True})
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)})


@habits_bp.route('/uncheck_by_date/<int:habit_id>', methods=['POST'])
@login_required
def habit_uncheck_by_date(habit_id):
    data = request.get_json()
    try:
        cd = datetime.strptime(data.get('date', ''), '%Y-%m-%d').date()
        record_uncheck(habit_id, current_user.id, checkin_date=cd)
        return jsonify({'success': True})
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)})


@habits_bp.route('/checkin_logs/<int:habit_id>')
@login_required
def checkin_logs(habit_id):
    habit = get_habit_or_none(habit_id, current_user.id)
    if not habit:
        flash('Habit does not exist', 'danger')
        return redirect(url_for('habits.my_habits'))
    return render_template(
        'checkin_logs.html',
        title=f"Check-in Logs - {habit.habit_name}",
        habit=habit,
        records=get_records_for_habit(habit_id, with_notes_only=True),
    )


@habits_bp.route('/delete_log/<int:log_id>', methods=['POST'])
@login_required
def habit_delete_log(log_id):
    record, err = get_record_or_json_error(log_id, current_user.id)
    if err:
        return err
    try:
        db.session.delete(record)
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)})
