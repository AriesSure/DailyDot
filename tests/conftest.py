"""pytest fixtures — app, db, client, authenticated requests."""

import pytest
from app.factory import create_app
from app.extensions import db as _db


@pytest.fixture(scope='function')
def app():
    """Create a fresh testing app for each test."""
    application = create_app('testing')
    application.config['WTF_CSRF_ENABLED'] = False
    with application.app_context():
        _db.create_all()
        yield application
        _db.session.rollback()
        _db.drop_all()


@pytest.fixture(scope='function')
def db(app):
    """Provide the SQLAlchemy database instance within an app context."""
    with app.app_context():
        yield _db


@pytest.fixture(scope='function')
def client(app):
    """Flask test client."""
    return app.test_client()


@pytest.fixture(scope='function')
def auth_headers(client):
    """Register + login a test user and return CSRF headers."""
    client.post('/auth/register', data={
        'username': 'testuser',
        'email': 'test@example.com',
        'password': 'Test@1234',
        'confirm': 'Test@1234',
    }, follow_redirects=True)
    client.post('/auth/login', data={
        'username': 'testuser',
        'password': 'Test@1234',
        'remember_me': 'y',
    }, follow_redirects=True)
    return {'X-CSRFToken': 'test', 'Content-Type': 'application/json'}


@pytest.fixture(scope='function')
def sample_habit(client, auth_headers):
    """Create a sample habit and return its id."""
    r = client.post('/habits/new', json={
        'habit_name': 'Morning Run',
        'icon': 'fas fa-running',
        'frequency': 'Every day this week',
        'time_period': 'Morning',
        'note': 'Test habit',
    }, headers=auth_headers)
    return r.get_json()


@pytest.fixture(scope='function')
def sample_todo(client, auth_headers):
    """Create a sample todo and return its id."""
    r = client.post('/todos/new', json={
        'event': 'Buy groceries',
        'date': '08/07/2026',
        'time': '18:00',
        'note': 'Test todo',
    }, headers=auth_headers)
    return r.get_json()
