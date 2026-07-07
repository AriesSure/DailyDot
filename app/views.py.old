
from flask import render_template, jsonify, redirect, url_for, flash, request
from app import app
from app.extensions import db, csrf
from app.models import User, Habit, Record, Todo, Card
from app.forms import ChooseForm, LoginForm, ChangePasswordForm, RegisterForm, HabitForm, CheckinForm, TodoForm
from flask_login import current_user, login_user, logout_user, login_required, fresh_login_required
import sqlalchemy as sa
from urllib.parse import urlsplit
from sqlalchemy.exc import OperationalError
from datetime import datetime, date, time, timedelta
from sqlalchemy.sql import and_
import requests
import random
import os
import time
import base64
import uuid
import shutil



@app.route("/")
def home():
    if current_user.is_authenticated:
        try:
            today = date.today()

            # Define the sort order for time_period
            time_period_order = sa.case(
                (Habit.time_period == '', 0),
                (Habit.time_period == 'Any time', 1),
                (Habit.time_period == 'After waking up', 2),
                (Habit.time_period == 'Morning', 3),
                (Habit.time_period == 'Noon', 4),
                (Habit.time_period == 'Afternoon', 5),
                (Habit.time_period == 'Evening', 6),
                (Habit.time_period == 'Before bedtime', 7),
                else_=8
            )

            unchecked_habits = db.session.query(Habit).outerjoin(Record, and_(
                Habit.id == Record.habit_id,
                Record.checkin_date == today
            )).filter(Habit.user_id == current_user.id,
                      Record.id.is_(None)).order_by(time_period_order).all()

            checked_habits = db.session.query(Habit).join(Record, and_(
                Habit.id == Record.habit_id,
                Record.checkin_date == today
            )).filter(Habit.user_id == current_user.id).order_by(time_period_order).all()

            # Get today's todos
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


@app.route("/account")
@login_required
def account():
    return render_template('account.html', title="Account")


@app.route("/my_habits")
@login_required
def my_habits():
    # Define the sort order for time_period
    time_period_order = sa.case(
        (Habit.time_period == '', 0),
        (Habit.time_period == 'Any time', 1),
        (Habit.time_period == 'After waking up', 2),
        (Habit.time_period == 'Morning', 3),
        (Habit.time_period == 'Noon', 4),
        (Habit.time_period == 'Afternoon', 5),
        (Habit.time_period == 'Evening', 6),
        (Habit.time_period == 'Before bedtime', 7),
        else_=8
    )

    habits = Habit.query.filter_by(user_id=current_user.id).order_by(time_period_order).all()
    return render_template('my_habits.html', title="My Habits", habits=habits)


@app.route("/habit/view/<int:habit_id>")
@login_required
def habit_view(habit_id):
    habit = db.session.query(Habit).filter_by(id=habit_id, user_id=current_user.id).first()
    if not habit:
        flash('Habit does not exist', 'danger')
        return redirect(url_for('my_habits'))

    # Get the number of check-ins for this month and this week
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

    # Retrieve all check-in records with notes, sorted in order by time
    recent_records = db.session.query(Record).filter(
        Record.habit_id == habit_id,
        Record.note.isnot(None),
        Record.note != ''
    ).order_by(Record.checkin_date.desc(), Record.checkin_time.desc()).all()

    # Get all check-in dates for calendar display
    all_records = db.session.query(Record).filter(
        Record.habit_id == habit_id
    ).all()
    checkin_dates = [record.checkin_date.isoformat() for record in all_records]

    return render_template('habit_view.html',
                         title=f"View {habit.habit_name}",
                         habit=habit,
                         current_month_count=current_month_count,
                         current_week_count=current_week_count,
                         recent_records=recent_records,
                         checkin_dates=checkin_dates)


@app.route("/habit/edit/<int:habit_id>", methods=['GET', 'POST'])
@login_required
def habit_edit(habit_id):
    habit = db.session.query(Habit).filter_by(id=habit_id, user_id=current_user.id).first()
    if not habit:
        flash('Habit does not exist', 'danger')
        return redirect(url_for('my_habits'))

    form = HabitForm()

    # Icon selections
    form.icon.choices = [
        ('fas fa-sun', 'sun'),
        ('fas fa-bed', 'bed'),
        ('fas fa-book', 'book'),
        ('fas fa-music', 'music'),
        ('fas fa-dumbbell', 'dumbbell'),
        ('fas fa-coffee', 'coffee'),
        ('fas fa-pen', 'pen'),
        ('fas fa-laptop', 'laptop'),
        ('fas fa-heart', 'heart'),
        ('fas fa-running', 'running'),
        ('fas fa-swimmer', 'swimmer'),
        ('fas fa-bicycle', 'bicycle')
    ]

    if request.method == 'GET':
        # Pre-fill form data
        form.habit_name.data = habit.habit_name
        form.icon.data = habit.icon
        form.frequency.data = habit.frequency
        form.time_period.data = habit.time_period
        form.note.data = habit.note

    if form.validate_on_submit():
        try:
            # Check if the custom name already exists
            existing_habit = db.session.query(Habit).filter(
                Habit.habit_name == form.habit_name.data,
                Habit.user_id == current_user.id,
                Habit.id != habit_id
            ).first()

            if existing_habit:
                flash('Habit name already exists. Please choose a different name.', 'danger')
                return render_template('habit_edit.html', title='Edit Habit', form=form, habit=habit)

            # Update habit information
            habit.habit_name = form.habit_name.data
            habit.icon = form.icon.data
            habit.frequency = form.frequency.data
            habit.time_period = form.time_period.data
            habit.note = form.note.data

            db.session.commit()
            flash('Habit updated successfully!', 'success')
            return redirect(url_for('habit_view', habit_id=habit.id))

        except Exception as e:
            db.session.rollback()
            flash(f'Error updating habit: {str(e)}', 'danger')
            return render_template('habit_edit.html', title='Edit Habit', form=form, habit=habit)

    return render_template('habit_edit.html', title='Edit Habit', form=form, habit=habit)


@app.route('/habit/delete/<int:habit_id>', methods=['POST'])
@login_required
def habit_delete(habit_id):
    habit = db.session.query(Habit).filter_by(id=habit_id, user_id=current_user.id).first()
    if not habit:
        return jsonify({'success': False, 'message': 'Habit does not exist'})

    try:
        # Delete relevant check-in records
        db.session.query(Record).filter_by(habit_id=habit_id).delete()

        # Delete habit
        db.session.delete(habit)
        db.session.commit()

        return jsonify({'success': True, 'message': 'Habit deleted successfully'})

    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)})


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('home'))
    form = LoginForm()
    if form.validate_on_submit():
        user = db.session.scalar(
            sa.select(User).where(User.username == form.username.data))
        if user is None or not user.check_password(form.password.data):
            flash('Invalid username or password', 'danger')
            return redirect(url_for('login'))
        login_user(user, remember=form.remember_me.data)
        next_page = request.args.get('next')
        if not next_page or urlsplit(next_page).netloc != '':
            next_page = url_for('home')
        return redirect(next_page)
    return render_template('generic_form.html', title='Sign In', form=form)

@app.route('/change_pw', methods=['GET', 'POST'])
@fresh_login_required
def change_pw():
    form = ChangePasswordForm()
    if form.validate_on_submit():
        current_user.set_password(form.new_password.data)
        db.session.commit()
        flash('Password changed successfully', 'success')
        return redirect(url_for('home'))
    return render_template('generic_form.html', title='Change Password', form=form)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('home'))
    form = RegisterForm()
    if form.validate_on_submit():
        new_user = User(username=form.username.data, email=form.email.data)
        new_user.set_password(form.password.data)
        db.session.add(new_user)
        db.session.commit()
        return redirect(url_for('home'))
    return render_template('generic_form.html', title='Register', form=form)


@app.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('home'))

########################################################################################################################
@app.route('/habit/new', methods=['GET', 'POST'])
@login_required
def new_habit():
    # Processing JSON requests (JavaScript pop-ups)
    if request.is_json:
        data = request.get_json()
        try:
            # Check if the custom name already exists
            habit_exist = db.session.query(Habit).filter_by(habit_name=data.get('habit_name'), user_id=current_user.id).first()
            if habit_exist:
                return jsonify({'success': False, 'message': 'Habit name already exists. Please choose a different name.'})

            # Create new habit
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

    form.icon.choices = [
        ('fas fa-sun', 'sun'),
        ('fas fa-bed', 'bed'),
        ('fas fa-book', 'book'),
        ('fas fa-music', 'music'),
        ('fas fa-dumbbell', 'dumbbell'),
        ('fas fa-coffee', 'coffee'),
        ('fas fa-pen', 'pen'),
        ('fas fa-laptop', 'laptop'),
        ('fas fa-heart', 'heart'),
        ('fas fa-running', 'running'),
        ('fas fa-swimmer', 'swimmer'),
        ('fas fa-bicycle', 'bicycle')
    ]

    if form.validate_on_submit():
        try:
            # Check if the custom name already exists
            habit_exist = db.session.query(Habit).filter_by(habit_name=form.habit_name.data, user_id=current_user.id).first()
            if habit_exist:
                flash('Habit name already exists. Please choose a different name.', 'danger')
                return render_template('new_habit.html', title='New Habit', form=form)
            # Create new habit
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
            return redirect(url_for('home'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error createing habit: {str(e)}', 'danger')
            return render_template('new_habit.html', title='New Habit', form=form)

    return render_template('new_habit.html', title='New Habit', form=form)


@app.route('/check_in/<int:habit_id>', methods=['GET', 'POST'])
@login_required
def check_in(habit_id):
    habit = db.session.query(Habit).filter_by(id=habit_id, user_id=current_user.id).first()
    if not habit:
        if request.is_json:
            return jsonify({'success': False, 'message': 'Habit does not exist'})
        flash('Habit does not exist', 'danger')
        return redirect(url_for('home'))

    today = date.today()
    record_exist = db.session.query(Record).filter(
        Record.habit_id == habit_id,
        Record.checkin_date == today
    ).first()

    # Processing JSON requests (JavaScript pop-ups)
    if request.is_json:
        data = request.get_json()
        if record_exist:
            return jsonify({'success': False, 'message': 'Already checked in today'})

        try:
            # Change time format
            time_str = data.get('time', '')
            if time_str:
                time_obj = datetime.strptime(time_str, '%H:%M').time()
            else:
                time_obj = datetime.now().time()

            record = Record(
                habit_id=habit_id,
                checkin_date=today,
                checkin_time=time_obj,
                note=data.get('note', '')
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
            return redirect(url_for('home'))
        else:
            try:
                record = Record(habit_id=habit_id, checkin_date=today, checkin_time=form.time.data, note=form.note.data)
                db.session.add(record)
                db.session.commit()
                flash('Checked in successfully', 'success')
                return redirect(url_for('home'))
            except Exception as e:
                db.session.rollback()
                flash(f'Error saving check-in: {str(e)}', 'danger')
                return redirect(url_for('home'))

    return render_template('checkin.html', title='Check In', habit=habit, form=form, record_exist=record_exist)


@app.route('/uncheck/<int:habit_id>', methods=['GET', 'POST'])
@login_required
def uncheck(habit_id):
    habit = db.session.query(Habit).filter_by(id=habit_id, user_id=current_user.id).first()
    if not habit:
        flash('Habit does not exist', 'danger')
        return redirect(url_for('home'))

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
    return redirect(url_for('home'))


@app.route('/habit/log_clear_note/<int:log_id>', methods=['POST'])
@login_required
def habit_log_clear_note(log_id):
    record = db.session.query(Record).join(Habit).filter(Record.id == log_id, Habit.user_id == current_user.id).first()
    if not record:
        return jsonify({'success': False, 'message': 'Log does not exist or no permission'})
    try:
        record.note = ''
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)})

@app.route('/habit/checkin_by_date/<int:habit_id>', methods=['POST'])
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

    # Repeated clocking in is not allowed
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

@app.route('/habit/uncheck_by_date/<int:habit_id>', methods=['POST'])
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


@app.route('/habit/checkin_logs/<int:habit_id>')
@login_required
def checkin_logs(habit_id):
    habit = db.session.query(Habit).filter_by(id=habit_id, user_id=current_user.id).first()
    if not habit:
        flash('Habit does not exist', 'danger')
        return redirect(url_for('my_habits'))

    # Retrieve all check-in records with notes, sorted in order by time
    all_records = db.session.query(Record).filter(
        Record.habit_id == habit_id,
        Record.note.isnot(None),
        Record.note != ''
    ).order_by(Record.checkin_date.desc(), Record.checkin_time.desc()).all()

    return render_template('checkin_logs.html',
                         title=f"Check-in Logs - {habit.habit_name}",
                         habit=habit,
                         records=all_records)


@app.route('/habit/delete_log/<int:log_id>', methods=['POST'])
@login_required
def habit_delete_log(log_id):
    record = db.session.query(Record).join(Habit).filter(Record.id == log_id, Habit.user_id == current_user.id).first()
    if not record:
        return jsonify({'success': False, 'message': 'Log does not exist or no permission'})
    try:
        db.session.delete(record)
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)})

@app.route('/todo/new', methods=['GET', 'POST'])
@login_required
def new_todo():
    # Processing JSON requests (JavaScript pop-ups)
    if request.is_json:
        try:
            data = request.get_json()
            new_todo = Todo(
                event=data.get('event'),
                date=data.get('date'),
                time=data.get('time'),
                note=data.get('note', ''),
                user_id=current_user.id
            )
            db.session.add(new_todo)
            db.session.commit()
            return jsonify({'success': True, 'message': 'Todo created successfully'})
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'message': str(e)})

    # Traditional form submission
    form = TodoForm()
    if form.validate_on_submit():
        try:
            new_todo = Todo(
                event=form.event.data,
                date=form.date.data.strftime('%d/%m/%Y'),
                time=form.time.data.strftime('%H:%M'),
                note=form.note.data or '',
                user_id=current_user.id
            )
            db.session.add(new_todo)
            db.session.commit()
            flash('Todo created successfully!', 'success')
            return redirect(url_for('home'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error creating todo: {str(e)}', 'danger')

    return render_template('new_todo.html', title='New Todo', form=form)

@app.route('/todo/complete/<int:todo_id>', methods=['POST'])
@login_required
def complete_todo(todo_id):
    if request.is_json:
        try:
            todo = db.session.query(Todo).filter_by(id=todo_id, user_id=current_user.id).first()
            if not todo:
                return jsonify({'success': False, 'message': 'Todo not found'})

            # Toggle completion status
            todo.completed = not todo.completed
            db.session.commit()
            return jsonify({'success': True, 'message': 'Todo status updated successfully'})
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'message': str(e)})

    return jsonify({'success': False, 'message': 'Invalid request'})


@app.route('/todo/edit/<int:todo_id>', methods=['POST'])
@login_required
def edit_todo(todo_id):
    if request.is_json:
        try:
            todo = db.session.query(Todo).filter_by(id=todo_id, user_id=current_user.id).first()
            if not todo:
                return jsonify({'success': False, 'message': 'Todo not found'})

            data = request.get_json()
            
            # Update todo information
            todo.event = data.get('event')
            todo.date = data.get('date')
            todo.time = data.get('time')
            todo.note = data.get('note', '')
            
            db.session.commit()
            return jsonify({'success': True, 'message': 'Todo updated successfully'})
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'message': str(e)})

    return jsonify({'success': False, 'message': 'Invalid request'})


@app.route('/todo/delete/<int:todo_id>', methods=['POST'])
@login_required
def delete_todo(todo_id):
    if request.is_json:
        try:
            todo = db.session.query(Todo).filter_by(id=todo_id, user_id=current_user.id).first()
            if not todo:
                return jsonify({'success': False, 'message': 'Todo not found'})

            # Delete todo
            db.session.delete(todo)
            db.session.commit()
            return jsonify({'success': True, 'message': 'Todo deleted successfully'})
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'message': str(e)})

    return jsonify({'success': False, 'message': 'Invalid request'})

@app.route('/todo/list')
@login_required
def todo_list():
    try:
        # Get all todos for the user, ordered by planned execution time (newest first)
        # Convert date string to proper date format for sorting
        todos = db.session.query(Todo).filter(
            Todo.user_id == current_user.id
        ).order_by(Todo.date.desc(), Todo.time.desc()).all()
        
        # Sort todos by actual date (not string) and time
        def parse_todo_date(todo):
            try:
                # Parse DD/MM/YYYY format
                day, month, year = todo.date.split('/')
                return (int(year), int(month), int(day), todo.time)
            except:
                return (1900, 1, 1, todo.time)
        
        todos.sort(key=parse_todo_date, reverse=True)

        return render_template('todo_list.html', title='Todo List', todos=todos)
    except Exception as e:
        db.session.rollback()
        flash(f'Failed to retrieve todos: {str(e)}', 'danger')
        return render_template('todo_list.html', title='Todo List', todos=[])

@app.route('/statistics')
@login_required
def statistics():
    try:
        # Get user's habits and records
        habits = db.session.query(Habit).filter_by(user_id=current_user.id).all()

        # Get check-in data for the last 30 days
        thirty_days_ago = date.today() - timedelta(days=30)
        recent_records = db.session.query(Record).join(Habit).filter(
            Habit.user_id == current_user.id,
            Record.checkin_date >= thirty_days_ago
        ).all()

        # Get todo completion data
        todos = db.session.query(Todo).filter_by(user_id=current_user.id).all()
        completed_todos = [todo for todo in todos if todo.completed]

        # Prepare data for charts - use the same method as MyHabits page
        habit_stats = []
        for habit in habits:
            # Use the same completed_count() method as MyHabits page
            total_checkins = habit.completed_count()
            habit_stats.append({
                'name': habit.habit_name,
                'total_checkins': total_checkins,
                'icon': habit.icon
            })

        # Sort habits by check-in count
        habit_stats.sort(key=lambda x: x['total_checkins'], reverse=True)

        # Get daily check-in data for the last 14 days (changed from 30)
        daily_data = []
        for i in range(14):
            day = date.today() - timedelta(days=i)
            day_records = [r for r in recent_records if r.checkin_date == day]
            daily_data.append({
                'date': day.strftime('%m/%d'),
                'count': len(day_records)
            })
        daily_data.reverse()  # Show oldest to newest

        # Todo completion rate
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
        return render_template('statistics.html',
                             title='Statistics',
                             habit_stats=[],
                             daily_data=[],
                             todo_completion_rate=0,
                             total_todos=0,
                             completed_todos=0)

@app.route('/habit/annual/<int:habit_id>')
@login_required
def annual_statistics(habit_id):
    try:
        habit = db.session.query(Habit).filter_by(id=habit_id, user_id=current_user.id).first()
        if not habit:
            flash('Habit does not exist', 'danger')
            return redirect(url_for('my_habits'))

        # Get current year or requested year from query parameter
        year = request.args.get('year', date.today().year, type=int)

        # Get all check-in records for the specified year
        start_date = date(year, 1, 1)
        end_date = date(year, 12, 31)

        records = db.session.query(Record).filter(
            Record.habit_id == habit_id,
            Record.checkin_date >= start_date,
            Record.checkin_date <= end_date
        ).all()

        # Create a set of check-in dates for quick lookup
        checkin_dates = {record.checkin_date.strftime('%Y-%m-%d') for record in records}

        # Prepare monthly data
        monthly_data = []
        for month in range(1, 13):
            month_start = date(year, month, 1)
            month_end = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)

            # Get first day of week for this month (0 = Monday, 6 = Sunday)
            first_day_of_week = month_start.weekday()

            # Get number of days in this month
            days_in_month = (month_end - month_start).days + 1

            # Create a list of check-in status for each day of the month
            daily_status = []
            for day in range(1, days_in_month + 1):
                checkin_date = date(year, month, day)
                daily_status.append(checkin_date.strftime('%Y-%m-%d') in checkin_dates)

            month_data = {
                'month_name': month_start.strftime('%B'),
                'year': year,
                'first_day_of_week': first_day_of_week,
                'days_in_month': days_in_month,
                'daily_status': daily_status
            }
            monthly_data.append(month_data)

        return render_template('annual.html',
                             title=f'{habit.habit_name} - Annual Statistics',
                             habit=habit,
                             monthly_data=monthly_data,
                             current_year=year)
    except Exception as e:
        db.session.rollback()
        flash(f'Failed to load annual statistics: {str(e)}', 'danger')
        return redirect(url_for('my_habits'))


@app.route('/card/new', methods=['GET', 'POST'])
@login_required
def new_card():
    try:
        today = date.today()

        # Calculate streak days (days since first check-in)
        first_record = db.session.query(Record).join(Habit).filter(
            Habit.user_id == current_user.id
        ).order_by(Record.checkin_date.asc()).first()

        streak_days = 0
        earliest_checkin_date_iso = today.isoformat()
        if first_record:
            streak_days = (today - first_record.checkin_date).days + 1
            earliest_checkin_date_iso = first_record.checkin_date.isoformat()

        # Count today's completed habits
        today_completed_habits = db.session.query(Record).join(Habit).filter(
            Habit.user_id == current_user.id,
            Record.checkin_date == today
        ).count()

        # Format data for template
        today_date = today.strftime('%B %d, %Y')
        weekday = today.strftime('%A')

        return render_template('new_card.html',
                             title='Create Achievement Card',
                             today_date=today_date,
                             weekday=weekday,
                             streak_days=streak_days,
                             earliest_checkin_date=earliest_checkin_date_iso,
                             completed_today=today_completed_habits,
                             motivational_quote="Every day is a new opportunity to be better.")

    except Exception as e:
        db.session.rollback()
        flash(f'Failed to load card page: {str(e)}', 'danger')
        return redirect(url_for('home'))


@app.route('/api/card/save', methods=['POST'])
@csrf.exempt
@login_required
def save_card_api():
    """Save a card for the selected date to the database. Enforces one card per day per user.
    Request JSON: { image_url, quote, weekday, streak, habits_completed, selected_date?: 'YYYY-MM-DD', overwrite=false }
    """
    try:
        data = request.get_json(force=True)
        # Use selected_date if provided, else fallback to today
        selected_date_str = data.get('selected_date')
        try:
            target_date = date.fromisoformat(selected_date_str) if selected_date_str else date.today()
        except Exception:
            target_date = date.today()

        # Find existing card for the target date
        existing = db.session.query(Card).filter_by(user_id=current_user.id, date=target_date).first()

        # If exists and not overwrite, return a flag to ask for confirmation
        overwrite = bool(data.get('overwrite'))
        if existing and not overwrite:
            return jsonify({ 'success': False, 'reason': 'exists' }), 200

        def _save_image_locally(src_url: str, target_date_val: date) -> str:
            """Download external image and store under static/cards/<user_id>/<date>.jpg (overwrite if exists).
            Return a /static/... URL with ?v=<ts> to bypass browser cache. If download fails, return empty string.
            """
            try:
                if not src_url or not src_url.startswith('http'):
                    return ''
                user_dir = os.path.join(app.root_path, 'static', 'cards', str(current_user.id))
                os.makedirs(user_dir, exist_ok=True)
                ts = int(time.time())
                filename = f"{target_date_val.isoformat()}.jpg"
                filepath = os.path.join(user_dir, filename)
                resp = requests.get(src_url, timeout=15)
                resp.raise_for_status()
                with open(filepath, 'wb') as f:
                    f.write(resp.content)
                return url_for('static', filename=f'cards/{current_user.id}/{filename}') + f'?v={ts}'
            except Exception:
                return ''

        def _save_image_bytes_to_local(image_bytes: bytes, target_date_val: date) -> str:
            try:
                if not image_bytes:
                    return ''
                user_dir = os.path.join(app.root_path, 'static', 'cards', str(current_user.id))
                os.makedirs(user_dir, exist_ok=True)
                ts = int(time.time())
                filename = f"{target_date_val.isoformat()}.jpg"
                filepath = os.path.join(user_dir, filename)
                with open(filepath, 'wb') as f:
                    f.write(image_bytes)
                return url_for('static', filename=f'cards/{current_user.id}/{filename}') + f'?v={ts}'
            except Exception:
                return ''

        incoming_image_url = data.get('image_url')
        incoming_image_data = data.get('image_data')  # base64 (data URL or raw base64)

        if existing and overwrite:
            # Prefer bytes from client to avoid any mismatch with remote URL
            saved_url = ''
            if incoming_image_data:
                try:
                    if incoming_image_data.startswith('data:'):
                        header, b64data = incoming_image_data.split(',', 1)
                    else:
                        b64data = incoming_image_data
                    image_bytes = base64.b64decode(b64data)
                    saved_url = _save_image_bytes_to_local(image_bytes, target_date)
                except Exception:
                    saved_url = ''
            if not saved_url and incoming_image_url:
                saved_url = _save_image_locally(incoming_image_url, target_date)
            if saved_url:
                existing.image_url = saved_url
            elif incoming_image_url:
                existing.image_url = incoming_image_url
            existing.quote = data.get('quote')
            existing.weekday = data.get('weekday')
            existing.streak = int(data.get('streak') or 0)
            existing.habits_completed = int(data.get('habits_completed') or 0)
            db.session.commit()
            return jsonify({ 'success': True, 'updated': True, 'card_id': existing.id })

        # Create new record
        # Save to local to ensure consistency; prefer bytes from client
        saved_url = ''
        incoming_image_data = data.get('image_data')
        if incoming_image_data:
            try:
                if incoming_image_data.startswith('data:'):
                    header, b64data = incoming_image_data.split(',', 1)
                else:
                    b64data = incoming_image_data
                image_bytes = base64.b64decode(b64data)
                saved_url = _save_image_bytes_to_local(image_bytes, target_date)
            except Exception:
                saved_url = ''
        if not saved_url and incoming_image_url:
            saved_url = _save_image_locally(incoming_image_url, target_date)
        card = Card(
            user_id=current_user.id,
            date=target_date,
            image_url=saved_url or incoming_image_url,
            quote=data.get('quote'),
            weekday=data.get('weekday'),
            streak=int(data.get('streak') or 0),
            habits_completed=int(data.get('habits_completed') or 0)
        )
        db.session.add(card)
        db.session.commit()
        return jsonify({ 'success': True, 'created': True, 'card_id': card.id })
    except OperationalError as oe:
        # Auto-create tables when 'cards' is missing, then retry once
        if 'no such table' in str(oe).lower():
            db.create_all()
            try:
                data = request.get_json(force=True)
                today = date.today()
                existing = db.session.query(Card).filter_by(user_id=current_user.id, date=today).first()
                overwrite = bool(data.get('overwrite'))
                if existing and not overwrite:
                    return jsonify({ 'success': False, 'reason': 'exists' }), 200
                if existing and overwrite:
                    incoming_image_url = data.get('image_url')
                    cached_url = _save_image_locally(incoming_image_url, today)
                    if cached_url:
                        existing.image_url = cached_url
                    elif incoming_image_url:
                        existing.image_url = incoming_image_url
                    existing.quote = data.get('quote')
                    existing.weekday = data.get('weekday')
                    existing.streak = int(data.get('streak') or 0)
                    existing.habits_completed = int(data.get('habits_completed') or 0)
                    db.session.commit()
                    return jsonify({ 'success': True, 'updated': True, 'card_id': existing.id })
                incoming_image_url = data.get('image_url')
                cached_url = _save_image_locally(incoming_image_url, today)
                card = Card(
                    user_id=current_user.id,
                    date=today,
                    image_url=cached_url or incoming_image_url,
                    quote=data.get('quote'),
                    weekday=data.get('weekday'),
                    streak=int(data.get('streak') or 0),
                    habits_completed=int(data.get('habits_completed') or 0)
                )
                db.session.add(card)
                db.session.commit()
                return jsonify({ 'success': True, 'created': True, 'card_id': card.id })
            except Exception as e2:
                db.session.rollback()
                return jsonify({ 'success': False, 'error': str(e2) }), 500
        db.session.rollback()
        return jsonify({ 'success': False, 'error': str(oe) }), 500
    except Exception as e:
        db.session.rollback()
        return jsonify({ 'success': False, 'error': str(e) }), 500


@app.route('/api/card/today')
@csrf.exempt
@login_required
def get_today_card_meta():
    """Return whether today's card already exists for current user."""
    try:
        selected_date_str = request.args.get('selected_date')
        try:
            target_date = date.fromisoformat(selected_date_str) if selected_date_str else date.today()
        except Exception:
            target_date = date.today()
        existing = db.session.query(Card).filter_by(user_id=current_user.id, date=target_date).first()
        return jsonify({ 'exists': bool(existing), 'card_id': existing.id if existing else None })
    except OperationalError as oe:
        if 'no such table' in str(oe).lower():
            db.create_all()
            selected_date_str = request.args.get('selected_date')
            try:
                target_date = date.fromisoformat(selected_date_str) if selected_date_str else date.today()
            except Exception:
                target_date = date.today()
            existing = db.session.query(Card).filter_by(user_id=current_user.id, date=target_date).first()
            return jsonify({ 'exists': bool(existing), 'card_id': existing.id if existing else None })
        return jsonify({ 'exists': False, 'error': str(oe) }), 500


@app.route('/api/card/image/<category>')
@login_required
def get_card_image(category):
    """Get random image, store it temporarily under static/cards/tmp/<user_id>/<uuid>.jpg, and return local URL.
    This guarantees the exact pixels shown on the client are what we persist later when saving.
    """
    try:
        pexels_api_key = os.environ.get('PEXELS_API_KEY', '')

        category_mapping = {
            'nature': ['nature', 'landscape', 'forest', 'mountain', 'ocean', 'sky'],
            'abstract': ['abstract', 'pattern', 'texture', 'art', 'design'],
            'minimal': ['minimal', 'simple', 'clean', 'white', 'space'],
            'gradient': ['gradient', 'color', 'background', 'smooth'],
            'geometric': ['geometric', 'shapes', 'pattern', 'architecture']
        }
        search_terms = category_mapping.get(category, ['motivation', 'inspiration'])
        search_term = random.choice(search_terms)

        def _save_temp_image(from_url: str) -> str:
            try:
                tmp_dir = os.path.join(app.root_path, 'static', 'cards', 'tmp', str(current_user.id))
                os.makedirs(tmp_dir, exist_ok=True)
                filename = uuid.uuid4().hex + '.jpg'
                filepath = os.path.join(tmp_dir, filename)
                resp = requests.get(from_url, timeout=12)
                resp.raise_for_status()
                with open(filepath, 'wb') as f:
                    f.write(resp.content)
                return url_for('static', filename=f'cards/tmp/{current_user.id}/{filename}')
            except Exception:
                return ''

        # Prefer Pexels when key is available
        if pexels_api_key:
            headers = { 'Authorization': pexels_api_key }
            params = { 'query': search_term, 'per_page': 20, 'page': random.randint(1, 3), 'orientation': 'square' }
            response = requests.get('https://api.pexels.com/v1/search', headers=headers, params=params, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if data.get('photos'):
                    photo = random.choice(data['photos'])
                    image_url = photo['src'].get('medium') or photo['src'].get('small') or photo['src'].get('original')
                    if image_url:
                        tmp_url = _save_temp_image(image_url)
                        if tmp_url:
                            return jsonify({ 'success': True, 'image_url': tmp_url, 'photographer': photo.get('photographer',''), 'source': 'pexels-local' })

        # Fallback to Lorem Picsum
        fallback_url = f"https://picsum.photos/400/400?random={random.randint(1, 1000)}"
        tmp_url = _save_temp_image(fallback_url)
        if tmp_url:
            return jsonify({ 'success': True, 'image_url': tmp_url, 'source': 'picsum-local' })

        # Last resort: return external URL if temp save failed
        return jsonify({ 'success': True, 'image_url': fallback_url, 'source': 'picsum' })

    except Exception as e:
        # Final fallback
        fb = f"https://picsum.photos/400/400?random={random.randint(1, 1000)}"
        return jsonify({ 'success': True, 'image_url': fb, 'source': 'picsum', 'error': str(e) })


@app.route('/api/card/quote/<category>')
@login_required
def get_card_quote(category):
    """Get a random motivational quote based on category"""
    try:
        quotes = {
            'motivation': [
                "Every day is a new opportunity to be better.",
                "Success is the sum of small efforts repeated day in and day out.",
                "The journey of a thousand miles begins with one step.",
                "Believe you can and you're halfway there.",
                "Your only limit is your mind."
            ],
            'success': [
                "Success doesn't just find you. You have to go out and get it.",
                "The harder you work for something, the greater you'll feel when you achieve it.",
                "Great things never come from comfort zones.",
                "Dream it. Wish it. Do it.",
                "Success is not final, failure is not fatal: it is the courage to continue that counts."
            ],
            'persistence': [
                "Don't stop when you're tired. Stop when you're done.",
                "It's not whether you get knocked down, it's whether you get up.",
                "Difficult roads often lead to beautiful destinations.",
                "Fall seven times, stand up eight.",
                "The difference between ordinary and extraordinary is that little extra."
            ],
            'growth': [
                "Be yourself; everyone else is already taken.",
                "You are never too old to set another goal or to dream a new dream.",
                "The only way to do great work is to love what you do.",
                "Growth begins at the end of your comfort zone.",
                "What lies behind us and what lies before us are tiny matters compared to what lies within us."
            ],
            'daily': [
                "Wake up with determination. Go to bed with satisfaction.",
                "Do something today that your future self will thank you for.",
                "Little progress is still progress.",
                "Great things take time.",
                "Today is a good day to have a good day."
            ]
        }

        # Get quotes for the specified category, fallback to motivation
        category_quotes = quotes.get(category, quotes['motivation'])
        selected_quote = random.choice(category_quotes)

        return jsonify({
            'success': True,
            'quote': selected_quote,
            'category': category
        })

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'quote': "Every day is a new opportunity to be better."
        })


@app.route('/card/stack')
@login_required
def card_stack():
    try:
        # Fetch saved cards for current user in reverse chronological order
        try:
            cards = (db.session.query(Card)
                     .filter_by(user_id=current_user.id)
                     .order_by(Card.date.desc(), Card.id.desc())
                     .all())
        except OperationalError as oe:
            if 'no such table' in str(oe).lower():
                db.create_all()
                cards = (db.session.query(Card)
                         .filter_by(user_id=current_user.id)
                         .order_by(Card.date.desc(), Card.id.desc())
                         .all())
            else:
                raise

        # Prepare dicts for template consumption
        cards_payload = []
        for c in cards:
            cards_payload.append({
                'id': c.id,
                'date': c.date.strftime('%b %d, %Y'),
                'weekday': c.weekday or c.date.strftime('%a'),
                'quote': c.quote or '',
                'streak_days': c.streak,
                'completed_habits': c.habits_completed,
                'image_url': c.image_url or ''
            })

        return render_template('card_stack.html',
                             title='Card Stack',
                             cards=cards_payload)

    except Exception as e:
        flash(f'Failed to load card stack: {str(e)}', 'danger')
        return redirect(url_for('account'))


@app.route('/api/card/<int:card_id>', methods=['DELETE'])
@csrf.exempt
@login_required
def delete_card(card_id):
    """Delete a card by id if it belongs to current user."""
    try:
        card = db.session.query(Card).filter_by(id=card_id, user_id=current_user.id).first()
        if not card:
            return jsonify({ 'success': False, 'error': 'Not found' }), 404
        db.session.delete(card)
        db.session.commit()
        return jsonify({ 'success': True })
    except Exception as e:
        db.session.rollback()
        return jsonify({ 'success': False, 'error': str(e) }), 500