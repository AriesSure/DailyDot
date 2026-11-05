"""pytest fixtures — app, db, client, authenticated requests."""

import pytest
from app.factory import create_app
from app.extensions import db as _db
from tests.database_lifecycle import isolated_database
from tests.network_boundary import NetworkBoundary, redact_test_report


def pytest_sessionstart(session):
    # Collection can import SDKs too: install before collecting any test module.
    session.config._io_patch = pytest.MonkeyPatch()
    session.config._io_boundary = NetworkBoundary()
    session.config._io_boundary.install(session.config._io_patch)


def pytest_sessionfinish(session, exitstatus):
    if session.config._io_boundary.attempts:
        session.exitstatus = 1
        reporter = session.config.pluginmanager.get_plugin('terminalreporter')
        if reporter:
            reporter.write_line('Forbidden I/O recorded during test session.', red=True)
    session.config._io_patch.undo()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    redact_test_report(report)


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
    boundary = NetworkBoundary()
    boundary.install(monkeypatch)
    monkeypatch.setenv('LLM_API_KEY', '')
    monkeypatch.setenv('HF_HUB_OFFLINE', '1')
    monkeypatch.setenv('TRANSFORMERS_OFFLINE', '1')
    yield
    assert boundary.attempts == [], 'A test attempted external I/O, even if its error was caught.'


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
