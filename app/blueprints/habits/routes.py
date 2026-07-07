"""Habits blueprint — CRUD, check-in / uncheck, logs."""

from flask import Blueprint, render_template, jsonify, redirect, url_for, flash, request
from flask_login import current_user, login_required
from datetime import date, time, datetime, timedelta
import sqlalchemy as sa

from app.extensions import db
from app.models import Habit, Record
from app.forms import HabitForm, CheckinForm
from app.utils.constants import ICON_CHOICES
from app.utils.query_helpers import get_time_period_order

habits_bp = Blueprint('habits', __name__, url_prefix='/habits')


@habits_bp.route('/')
@login_required
def my_habits():
    time_period_order = get_time_period_order()
    habits = Habit.query.filter_by(user_id=current_user.id).order_by(time_period_order).all()
    return render_template('my_habits.html', title="My Habits", habits=habits)


@habits_bp.route('/new', methods=['GET', 'POST'])
@login_required
def new_habit():
    if request.is_json:
        data = request.get_json()
        try:
            habit_exist = db.session.query(Habit).filter_by(
                habit_name=data.get('habit_name'), user_id=current_user.id).first()
            if habit_exist:
                return jsonify({'success': False, 'message': 'Habit name already exists.'})
            habit = Habit(
                habit_name=data.get('habit_name'),
                icon=data.get('icon'),
                frequency=data.get('frequency'),
                time_period=data.get('time_period'),
                note=data.get('note', ''),
                user_id=current_user.id
            )
            db.session.add(habit)
            db.session.commit()
            return jsonify({'success': True, 'message': 'Habit created successfully!'})
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'message': f'Error creating habit: {str(e)}'})

    form = HabitForm()
    form.icon.choices = ICON_CHOICES

    if form.validate_on_submit():
        try:
            habit_exist = db.session.query(Habit).filter_by(
                habit_name=form.habit_name.data, user_id=current_user.id).first()
            if habit_exist:
                flash('Habit name already exists.', 'danger')
                return render_template('new_habit.html', title='New Habit', form=form)
            habit = Habit(
                habit_name=form.habit_name.data,
                icon=form.icon.data,
                frequency=form.frequency.data,
                time_period=form.time_period.data,
                note=form.note.data,
                user_id=current_user.id
            )
            db.session.add(habit)
            db.session.commit()
            flash('Habit created successfully!', 'success')
            return redirect(url_for('main.home'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error creating habit: {str(e)}', 'danger')
            return render_template('new_habit.html', title='New Habit', form=form)

    return render_template('new_habit.html', title='New Habit', form=form)


@habits_bp.route('/view/<int:habit_id>')
@login_required
def habit_view(habit_id):
    habit = db.session.query(Habit).filter_by(id=habit_id, user_id=current_user.id).first()
    if not habit:
        flash('Habit does not exist', 'danger')
        return redirect(url_for('habits.my_habits'))

    today = date.today()
    current_month_start = date(today.year, today.month, 1)
    current_week_start = today - timedelta(days=today.weekday())

    current_month_count = db.session.query(Record).filter(
        Record.habit_id == habit_id,
        Record.checkin_date >= current_month_start
    ).count()

    current_week_count = db.session.query(Record).filter(
        Record.habit_id == habit_id,
        Record.checkin_date >= current_week_start
    ).count()

    recent_records = db.session.query(Record).filter(
        Record.habit_id == habit_id,
        Record.note.isnot(None),
        Record.note != ''
    ).order_by(Record.checkin_date.desc(), Record.checkin_time.desc()).all()

    all_records = db.session.query(Record).filter(Record.habit_id == habit_id).all()
    checkin_dates = [record.checkin_date.isoformat() for record in all_records]

    return render_template('habit_view.html',
                           title=f"View {habit.habit_name}",
                           habit=habit,
                           current_month_count=current_month_count,
                           current_week_count=current_week_count,
                           recent_records=recent_records,
                           checkin_dates=checkin_dates)


@habits_bp.route('/edit/<int:habit_id>', methods=['GET', 'POST'])
@login_required
def habit_edit(habit_id):
    habit = db.session.query(Habit).filter_by(id=habit_id, user_id=current_user.id).first()
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
            existing_habit = db.session.query(Habit).filter(
                Habit.habit_name == form.habit_name.data,
                Habit.user_id == current_user.id,
                Habit.id != habit_id
            ).first()
            if existing_habit:
                flash('Habit name already exists.', 'danger')
                return render_template('habit_edit.html', title='Edit Habit', form=form, habit=habit)
            habit.habit_name = form.habit_name.data
            habit.icon = form.icon.data
            habit.frequency = form.frequency.data
            habit.time_period = form.time_period.data
            habit.note = form.note.data
            db.session.commit()
            flash('Habit updated successfully!', 'success')
            return redirect(url_for('habits.habit_view', habit_id=habit.id))
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating habit: {str(e)}', 'danger')
            return render_template('habit_edit.html', title='Edit Habit', form=form, habit=habit)

    return render_template('habit_edit.html', title='Edit Habit', form=form, habit=habit)


@habits_bp.route('/delete/<int:habit_id>', methods=['POST'])
@login_required
def habit_delete(habit_id):
    habit = db.session.query(Habit).filter_by(id=habit_id, user_id=current_user.id).first()
    if not habit:
        return jsonify({'success': False, 'message': 'Habit does not exist'})
    try:
        db.session.query(Record).filter_by(habit_id=habit_id).delete()
        db.session.delete(habit)
        db.session.commit()
        return jsonify({'success': True, 'message': 'Habit deleted successfully'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)})


@habits_bp.route('/check_in/<int:habit_id>', methods=['GET', 'POST'])
@login_required
def check_in(habit_id):
    habit = db.session.query(Habit).filter_by(id=habit_id, user_id=current_user.id).first()
    if not habit:
        if request.is_json:
            return jsonify({'success': False, 'message': 'Habit does not exist'})
        flash('Habit does not exist', 'danger')
        return redirect(url_for('main.home'))

    today = date.today()
    record_exist = db.session.query(Record).filter(
        Record.habit_id == habit_id,
        Record.checkin_date == today
    ).first()

    if request.is_json:
        data = request.get_json()
        if record_exist:
            return jsonify({'success': False, 'message': 'Already checked in today'})
        try:
            time_str = data.get('time', '')
            if time_str:
                time_obj = datetime.strptime(time_str, '%H:%M').time()
            else:
                time_obj = datetime.now().time()
            record = Record(
                habit_id=habit_id, checkin_date=today,
                checkin_time=time_obj, note=data.get('note', '')
            )
            db.session.add(record)
            db.session.commit()
            return jsonify({'success': True, 'message': 'Checked in successfully'})
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'message': f'Error saving check-in: {str(e)}'})

    form = CheckinForm()
    if request.method == 'GET':
        form.time.data = datetime.now().time()
    if request.method == 'POST' and form.validate_on_submit():
        if record_exist:
            flash('Already checked in today', 'warning')
            return redirect(url_for('main.home'))
        try:
            record = Record(habit_id=habit_id, checkin_date=today,
                            checkin_time=form.time.data, note=form.note.data)
            db.session.add(record)
            db.session.commit()
            flash('Checked in successfully', 'success')
            return redirect(url_for('main.home'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error saving check-in: {str(e)}', 'danger')
            return redirect(url_for('main.home'))

    return render_template('checkin.html', title='Check In', habit=habit, form=form, record_exist=record_exist)


@habits_bp.route('/uncheck/<int:habit_id>', methods=['GET', 'POST'])
@login_required
def uncheck(habit_id):
    habit = db.session.query(Habit).filter_by(id=habit_id, user_id=current_user.id).first()
    if not habit:
        flash('Habit does not exist', 'danger')
        return redirect(url_for('main.home'))
    today = date.today()
    record = db.session.query(Record).filter_by(habit_id=habit_id, checkin_date=today).first()
    if not record:
        flash('No check-in record found', 'warning')
    else:
        try:
            db.session.delete(record)
            db.session.commit()
            flash('Unchecked successfully', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Error unchecking: {str(e)}', 'danger')
    return redirect(url_for('main.home'))


@habits_bp.route('/log_clear_note/<int:log_id>', methods=['POST'])
@login_required
def habit_log_clear_note(log_id):
    from app.utils.query_helpers import get_record_or_json_error
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
    habit = db.session.query(Habit).filter_by(id=habit_id, user_id=current_user.id).first()
    if not habit:
        return jsonify({'success': False, 'message': 'Habit does not exist'})
    data = request.get_json()
    date_str = data.get('date')
    note = data.get('note', '')
    time_str = data.get('time', '')
    try:
        checkin_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        checkin_time = datetime.strptime(time_str, '%H:%M').time() if time_str else datetime.now().time()
    except Exception:
        return jsonify({'success': False, 'message': 'Invalid date or time'})
    record_exist = db.session.query(Record).filter_by(habit_id=habit_id, checkin_date=checkin_date).first()
    if record_exist:
        return jsonify({'success': False, 'message': 'Already checked in for this day'})
    try:
        record = Record(habit_id=habit_id, checkin_date=checkin_date, checkin_time=checkin_time, note=note)
        db.session.add(record)
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)})


@habits_bp.route('/uncheck_by_date/<int:habit_id>', methods=['POST'])
@login_required
def habit_uncheck_by_date(habit_id):
    habit = db.session.query(Habit).filter_by(id=habit_id, user_id=current_user.id).first()
    if not habit:
        return jsonify({'success': False, 'message': 'Habit does not exist'})
    data = request.get_json()
    date_str = data.get('date')
    try:
        checkin_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except Exception:
        return jsonify({'success': False, 'message': 'Invalid date'})
    record = db.session.query(Record).filter_by(habit_id=habit_id, checkin_date=checkin_date).first()
    if not record:
        return jsonify({'success': False, 'message': 'No check-in record for this day'})
    try:
        db.session.delete(record)
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)})


@habits_bp.route('/checkin_logs/<int:habit_id>')
@login_required
def checkin_logs(habit_id):
    habit = db.session.query(Habit).filter_by(id=habit_id, user_id=current_user.id).first()
    if not habit:
        flash('Habit does not exist', 'danger')
        return redirect(url_for('habits.my_habits'))
    all_records = db.session.query(Record).filter(
        Record.habit_id == habit_id,
        Record.note.isnot(None),
        Record.note != ''
    ).order_by(Record.checkin_date.desc(), Record.checkin_time.desc()).all()
    return render_template('checkin_logs.html',
                           title=f"Check-in Logs - {habit.habit_name}",
                           habit=habit,
                           records=all_records)


@habits_bp.route('/delete_log/<int:log_id>', methods=['POST'])
@login_required
def habit_delete_log(log_id):
    from app.utils.query_helpers import get_record_or_json_error
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
