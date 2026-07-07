"""Todo business logic."""

from datetime import datetime

from app.extensions import db
from app.models import Todo


def create_todo(event, date_str, time_str, user_id, note=''):
    """Create a new todo. *date_str* should be ``DD/MM/YYYY``, *time_str* ``HH:MM``."""
    todo = Todo(
        event=event,
        date=date_str,
        time=time_str,
        note=note,
        user_id=user_id,
    )
    db.session.add(todo)
    db.session.commit()
    return todo


def toggle_complete(todo_id, user_id):
    """Toggle the completed flag.  Raises ``ValueError`` if not found."""
    todo = db.session.query(Todo).filter_by(id=todo_id, user_id=user_id).first()
    if not todo:
        raise ValueError('Todo not found')
    todo.completed = not todo.completed
    db.session.commit()
    return todo


def update_todo(todo_id, user_id, event=None, date_str=None, time_str=None, note=None):
    """Update todo fields. Only provided fields are updated."""
    todo = db.session.query(Todo).filter_by(id=todo_id, user_id=user_id).first()
    if not todo:
        raise ValueError('Todo not found')
    if event is not None:
        todo.event = event
    if date_str is not None:
        todo.date = date_str
    if time_str is not None:
        todo.time = time_str
    if note is not None:
        todo.note = note
    db.session.commit()
    return todo


def delete_todo(todo_id, user_id):
    """Delete a todo.  Raises ``ValueError`` if not found."""
    todo = db.session.query(Todo).filter_by(id=todo_id, user_id=user_id).first()
    if not todo:
        raise ValueError('Todo not found')
    db.session.delete(todo)
    db.session.commit()


def get_todos_for_user(user_id):
    """Return all todos for a user, sorted newest-first."""
    todos = db.session.query(Todo).filter(
        Todo.user_id == user_id
    ).order_by(Todo.date.desc(), Todo.time.desc()).all()

    # Sort by actual date (string is DD/MM/YYYY)
    def _sort_key(t):
        try:
            d, m, y = t.date.split('/')
            return (int(y), int(m), int(d), t.time)
        except Exception:
            return (1900, 1, 1, t.time)

    todos.sort(key=_sort_key, reverse=True)
    return todos
