"""Todos blueprint — CRUD + completion toggle."""

from flask import Blueprint, render_template, jsonify, redirect, url_for, flash, request
from flask_login import current_user, login_required

from app.extensions import db
from app.forms import TodoForm
from app.services.todo_service import (
    create_todo,
    toggle_complete,
    update_todo,
    delete_todo,
    get_todos_for_user,
)

todos_bp = Blueprint('todos', __name__, url_prefix='/todos')


@todos_bp.route('/new', methods=['GET', 'POST'])
@login_required
def new_todo():
    if request.is_json:
        data = request.get_json()
        try:
            create_todo(
                event=data.get('event'),
                date_str=data.get('date'),
                time_str=data.get('time'),
                note=data.get('note', ''),
                user_id=current_user.id,
            )
            return jsonify({'success': True, 'message': 'Todo created successfully'})
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'message': str(e)})

    form = TodoForm()
    if form.validate_on_submit():
        try:
            create_todo(
                event=form.event.data,
                date_str=form.date.data.strftime('%d/%m/%Y'),
                time_str=form.time.data.strftime('%H:%M'),
                note=form.note.data or '',
                user_id=current_user.id,
            )
            flash('Todo created successfully!', 'success')
            return redirect(url_for('main.home'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error creating todo: {str(e)}', 'danger')
    return render_template('new_todo.html', title='New Todo', form=form)


@todos_bp.route('/complete/<int:todo_id>', methods=['POST'])
@login_required
def complete_todo(todo_id):
    if not request.is_json:
        return jsonify({'success': False, 'message': 'Invalid request'})
    try:
        toggle_complete(todo_id, current_user.id)
        return jsonify({'success': True, 'message': 'Todo status updated successfully'})
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)})


@todos_bp.route('/edit/<int:todo_id>', methods=['POST'])
@login_required
def edit_todo(todo_id):
    if not request.is_json:
        return jsonify({'success': False, 'message': 'Invalid request'})
    data = request.get_json()
    try:
        update_todo(
            todo_id, current_user.id,
            event=data.get('event'),
            date_str=data.get('date'),
            time_str=data.get('time'),
            note=data.get('note', ''),
        )
        return jsonify({'success': True, 'message': 'Todo updated successfully'})
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)})


@todos_bp.route('/delete/<int:todo_id>', methods=['POST'])
@login_required
def delete_todo(todo_id):
    if not request.is_json:
        return jsonify({'success': False, 'message': 'Invalid request'})
    try:
        delete_todo(todo_id, current_user.id)
        return jsonify({'success': True, 'message': 'Todo deleted successfully'})
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)})


@todos_bp.route('/list')
@login_required
def todo_list():
    try:
        todos = get_todos_for_user(current_user.id)
        return render_template('todo_list.html', title='Todo List', todos=todos)
    except Exception as e:
        db.session.rollback()
        flash(f'Failed to retrieve todos: {str(e)}', 'danger')
        return render_template('todo_list.html', title='Todo List', todos=[])
