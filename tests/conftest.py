"""pytest fixtures — app, db, client, authenticated requests."""

import pytest
import socket
import sqlite3
import sqlite3.dbapi2

from sqlalchemy.engine import Engine
from app.factory import create_app
from app.extensions import db as _db
from tests.database_lifecycle import isolated_database


@pytest.fixture(scope='function')
def app(offline_test_boundary):
    """Create a fresh testing app for each test."""
    application = create_app('testing')
    application.config['WTF_CSRF_ENABLED'] = False
    with isolated_database(application, _db):
        yield application


@pytest.fixture(autouse=True)
def offline_test_boundary(monkeypatch):
    """Block and record external I/O, including errors swallowed by app fallback."""
    attempts = []

    def deny(*args, **kwargs):
        attempts.append('external_io_attempt')
        raise AssertionError('external_io_forbidden')

    monkeypatch.setattr(socket.socket, 'connect', deny)
    monkeypatch.setattr(socket, 'create_connection', deny)
    original_connect = Engine.connect

    def memory_connect(engine, *args, **kwargs):
        if str(engine.url) != 'sqlite:///:memory:':
            return deny()
        return original_connect(engine, *args, **kwargs)

    monkeypatch.setattr(Engine, 'connect', memory_connect)
    original_sqlite = sqlite3.dbapi2.connect

    def sqlite_memory_only(database, *args, **kwargs):
        if database != ':memory:':
            return deny()
        return original_sqlite(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, 'connect', sqlite_memory_only)
    monkeypatch.setattr(sqlite3.dbapi2, 'connect', sqlite_memory_only)
    import pymysql
    monkeypatch.setattr(pymysql.connections.Connection, 'connect', deny)
    import requests
    monkeypatch.setattr(requests.sessions.Session, 'request', deny)
    monkeypatch.setenv('LLM_API_KEY', '')
    monkeypatch.setenv('HF_HUB_OFFLINE', '1')
    monkeypatch.setenv('TRANSFORMERS_OFFLINE', '1')
    yield
    assert attempts == [], 'A test attempted external I/O, even if its error was caught.'


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
