"""Todos blueprint — CRUD + completion toggle."""

from flask import Blueprint, render_template, jsonify, redirect, url_for, flash, request
from flask_login import current_user, login_required
from datetime import datetime

from app.extensions import db
from app.models import Todo
from app.forms import TodoForm

todos_bp = Blueprint('todos', __name__, url_prefix='/todos')


@todos_bp.route('/new', methods=['GET', 'POST'])
@login_required
def new_todo():
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
            return redirect(url_for('main.home'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error creating todo: {str(e)}', 'danger')
    return render_template('new_todo.html', title='New Todo', form=form)


@todos_bp.route('/complete/<int:todo_id>', methods=['POST'])
@login_required
def complete_todo(todo_id):
    if request.is_json:
        try:
            todo = db.session.query(Todo).filter_by(id=todo_id, user_id=current_user.id).first()
            if not todo:
                return jsonify({'success': False, 'message': 'Todo not found'})
            todo.completed = not todo.completed
            db.session.commit()
            return jsonify({'success': True, 'message': 'Todo status updated successfully'})
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'message': str(e)})
    return jsonify({'success': False, 'message': 'Invalid request'})


@todos_bp.route('/edit/<int:todo_id>', methods=['POST'])
@login_required
def edit_todo(todo_id):
    if request.is_json:
        try:
            todo = db.session.query(Todo).filter_by(id=todo_id, user_id=current_user.id).first()
            if not todo:
                return jsonify({'success': False, 'message': 'Todo not found'})
            data = request.get_json()
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


@todos_bp.route('/delete/<int:todo_id>', methods=['POST'])
@login_required
def delete_todo(todo_id):
    if request.is_json:
        try:
            todo = db.session.query(Todo).filter_by(id=todo_id, user_id=current_user.id).first()
            if not todo:
                return jsonify({'success': False, 'message': 'Todo not found'})
            db.session.delete(todo)
            db.session.commit()
            return jsonify({'success': True, 'message': 'Todo deleted successfully'})
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'message': str(e)})
    return jsonify({'success': False, 'message': 'Invalid request'})


@todos_bp.route('/list')
@login_required
def todo_list():
    try:
        todos = db.session.query(Todo).filter(
            Todo.user_id == current_user.id
        ).order_by(Todo.date.desc(), Todo.time.desc()).all()

        def parse_todo_date(todo):
            try:
                day, month, year = todo.date.split('/')
                return (int(year), int(month), int(day), todo.time)
            except Exception:
                return (1900, 1, 1, todo.time)

        todos.sort(key=parse_todo_date, reverse=True)
        return render_template('todo_list.html', title='Todo List', todos=todos)
    except Exception as e:
        db.session.rollback()
        flash(f'Failed to retrieve todos: {str(e)}', 'danger')
        return render_template('todo_list.html', title='Todo List', todos=[])
