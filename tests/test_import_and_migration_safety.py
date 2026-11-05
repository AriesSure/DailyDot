"""Runtime checks for lazy imports, management contexts, and migration refusal."""

import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import textwrap
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import sqlalchemy as sa
from flask import Flask, current_app

from app.extensions import db
from app.factory import create_app


ROOT = Path(__file__).resolve().parents[1]


def run_isolated(source):
    environment = dict(os.environ)
    for name in ('DATABASE_URL', 'TEST_DATABASE_URL', 'TEST_DATABASE_ALLOW_MYSQL'):
        environment.pop(name, None)
    environment.update(FLASK_ENV='testing', TEST_DATABASE_PROFILE='sqlite-memory',
                       LLM_API_KEY='', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                       PYTHONDONTWRITEBYTECODE='1')
    guards = '''
import socket, sqlite3, sqlite3.dbapi2
import sqlalchemy as sa
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.engine import Engine
import pymysql
attempts = []
def forbidden(*args, **kwargs):
    attempts.append('forbidden_io_or_ddl')
    raise AssertionError('forbidden_io_or_ddl')
socket.socket.connect = forbidden
socket.socket.connect_ex = forbidden
socket.socket.sendto = forbidden
socket.create_connection = forbidden
Engine.connect = forbidden
Engine.raw_connection = forbidden
sqlite3.connect = forbidden
sqlite3.dbapi2.connect = forbidden
pymysql.connections.Connection.connect = forbidden
SQLAlchemy.create_all = forbidden
SQLAlchemy.drop_all = forbidden
sa.MetaData.create_all = forbidden
sa.MetaData.drop_all = forbidden
'''
    result = subprocess.run(
        [sys.executable, '-B', '-X', 'utf8', '-c', textwrap.dedent(guards) + '\n'
         + textwrap.dedent(source) + '\nassert attempts == []\n'],
        cwd=ROOT, env=environment, capture_output=True, text=True, encoding='utf-8', timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('statement', [
    'import app', 'from app.factory import create_app', 'from app import create_app',
    'import app.debug_utils', 'import manage_db',
])
def test_imports_do_not_construct_app_or_connect(statement):
    run_isolated('''
from flask import Flask
Flask.__init__ = forbidden
''' + statement + '''
import app
assert app._default_app is None
''')


def test_default_cache_aliases_and_explicit_factory():
    run_isolated('''
import app
from unittest.mock import Mock
original = app.create_app
factory = Mock(wraps=original)
app.create_app = factory
first = app.app
assert app.application is first
from app import app as imported_app
from app import application
assert imported_app is application is first
assert factory.call_count == 1
second = original('testing')
assert second is not first
assert app.app is first
for instance in (first, second):
    with instance.app_context():
        for engine in app.db.engines.values():
            engine.dispose()
''')


def test_failed_creation_retries_and_recursion_does_not_cache():
    run_isolated('''
import app
from unittest.mock import Mock
expected = object()
factory = Mock(side_effect=[RuntimeError('first_failure'), expected])
app.create_app = factory
try:
    app.app
except RuntimeError as error:
    assert str(error) == 'first_failure'
else:
    raise AssertionError('failure not propagated')
assert app._default_app is None
assert app.application is expected
assert factory.call_count == 2
app._default_app = None
app.create_app = lambda: app.app
try:
    app.application
except RuntimeError as error:
    assert 'Recursive default application' in str(error)
else:
    raise AssertionError('recursive initialization succeeded')
assert app._default_app is None
assert app._creating_default_app is False
''')


def test_concurrent_first_access_creates_once():
    run_isolated('''
import app, threading, time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock
barrier = threading.Barrier(8)
expected = object()
def construct():
    time.sleep(0.02)
    return expected
factory = Mock(side_effect=construct)
app.create_app = factory
def access(index):
    barrier.wait(timeout=5)
    return app.app if index % 2 else app.application
with ThreadPoolExecutor(max_workers=8) as executor:
    results = list(executor.map(access, range(8)))
assert all(result is expected for result in results)
assert factory.call_count == 1
''')


@pytest.mark.skipif(
    os.name == 'nt',
    reason=(
        'Gunicorn real loader requires POSIX; '
        'Windows lacks fcntl. Run this test on Linux CI.'
    ),
)
def test_gunicorn_real_loader_aliases():
    run_isolated('''
import app
from gunicorn.util import import_app
first = import_app('app:app')
second = import_app('app')
assert first is second is app.app is app.application
assert callable(first)
with first.app_context():
    app.db.engine.dispose()
''')


@pytest.mark.parametrize('testing', [False, True])
def test_management_context_never_switches_active_application(monkeypatch, testing):
    import app
    from app.debug_utils import database_management_context
    application = Flask(__name__)
    application.testing = testing
    factory = Mock(side_effect=AssertionError('default_app_forbidden'))
    monkeypatch.setattr(app, '_default_app', None)
    monkeypatch.setattr(app, 'create_app', factory)
    with application.app_context():
        if testing:
            with pytest.raises(RuntimeError, match='disabled in testing'):
                with database_management_context():
                    pytest.fail('testing management allowed')
        else:
            with database_management_context() as selected:
                assert selected is application
                assert current_app._get_current_object() is application
    factory.assert_not_called()


def test_management_without_context_resolves_default_lazily(monkeypatch):
    import app
    from app.debug_utils import database_management_context
    application = Flask(__name__)
    factory = Mock(return_value=application)
    monkeypatch.setattr(app, '_default_app', None)
    monkeypatch.setattr(app, 'create_app', factory)
    with database_management_context() as selected:
        assert selected is application
        assert current_app._get_current_object() is application
    factory.assert_called_once()


@pytest.mark.parametrize('name,args', [
    ('reset_db', ()), ('init_migrations', ()), ('upgrade_db', ()), ('create_migration', ('x',)),
])
def test_testing_helpers_reject_before_operations(monkeypatch, name, args):
    import app.debug_utils as helpers
    application = Flask(__name__)
    application.testing = True
    operations = [Mock() for _ in range(5)]
    for target, operation in zip(('upgrade', 'init', 'migrate'), operations):
        monkeypatch.setattr(helpers, target, operation)
    monkeypatch.setattr(db, 'create_all', operations[3])
    monkeypatch.setattr(db, 'drop_all', operations[4])
    with application.app_context(), pytest.raises(RuntimeError, match='disabled in testing'):
        getattr(helpers, name)(*args)
    for operation in operations:
        operation.assert_not_called()


@pytest.mark.parametrize('command', ['reset', 'init', 'upgrade', 'migrate', 'status'])
def test_manage_cli_rejects_testing_before_prompt(monkeypatch, command):
    import manage_db
    application = Flask(__name__)
    application.testing = True
    prompt = Mock(side_effect=AssertionError('prompt_not_allowed'))
    monkeypatch.setattr('builtins.input', prompt)
    monkeypatch.setattr(sys, 'argv', ['manage_db.py', command, 'message'])
    with application.app_context(), pytest.raises(RuntimeError, match='disabled in testing'):
        manage_db.main()
    prompt.assert_not_called()


@pytest.mark.parametrize('mode', ['development', 'production'])
def test_management_upgrade_preserves_non_testing_context(monkeypatch, mode):
    from app import debug_utils
    application = Flask(mode)
    upgrade = Mock()
    monkeypatch.setattr(debug_utils, 'upgrade', upgrade)
    with application.app_context():
        debug_utils.upgrade_db()
    upgrade.assert_called_once_with()


@pytest.mark.parametrize('arguments', [
    ['upgrade'], ['downgrade'], ['stamp'], ['migrate'], ['current'], ['upgrade', '--sql'],
])
def test_direct_migrate_cli_refuses_before_engine_or_dbapi_connection(monkeypatch, tmp_path, arguments,
                                                                    caplog):
    import sqlite3
    import sqlite3.dbapi2
    import pymysql
    from alembic import context
    watchers = [Mock(side_effect=AssertionError('unexpected_migration_work')) for _ in range(7)]
    monkeypatch.setattr(sa.engine.Engine, 'connect', watchers[0])
    monkeypatch.setattr(sa.engine.Engine, 'raw_connection', watchers[1])
    monkeypatch.setattr(sqlite3, 'connect', watchers[2])
    monkeypatch.setattr(sqlite3.dbapi2, 'connect', watchers[2])
    monkeypatch.setattr(pymysql.connections.Connection, 'connect', watchers[3])
    monkeypatch.setattr(context, 'configure', watchers[4])
    monkeypatch.setattr(context, 'run_migrations', watchers[5])
    # This checks rejection before reading/serializing an Engine URL as well.
    monkeypatch.setattr(sa.engine.URL, 'render_as_string', watchers[6])
    directory = tmp_path / 'migrations'
    shutil.copytree(ROOT / 'migrations', directory, ignore=shutil.ignore_patterns('__pycache__'))
    application = create_app('testing')
    with application.app_context():
        engine = db.engine
    try:
        before = sorted(p.name for p in (directory / 'versions').iterdir())
        result = application.test_cli_runner().invoke(
            args=['db', arguments[0], '-d', str(directory), *arguments[1:]],
        )
        assert result.exit_code != 0
        assert 'Alembic migrations are disabled in testing.' in result.output + caplog.text
        assert before == sorted(p.name for p in (directory / 'versions').iterdir())
        assert 'fake_test_secret_123' not in result.output + caplog.text
        for watcher in watchers:
            watcher.assert_not_called()
    finally:
        engine.dispose()


def test_alembic_without_flask_context_refuses():
    from alembic.util import CommandError
    with pytest.raises(CommandError, match='requires a Flask application context'):
        runpy.run_path(str(ROOT / 'migrations' / 'env.py'))


@pytest.mark.parametrize('mode', ['development', 'production'])
@pytest.mark.parametrize('offline', [False, True])
def test_non_testing_migration_flow_with_fake_connection(monkeypatch, mode, offline):
    from alembic import context
    from contextlib import nullcontext
    configuration = SimpleNamespace(config_file_name='unused', cmd_opts=None,
                                    set_main_option=Mock(), get_main_option=Mock(return_value='fake-url'))
    connection = object()
    engine = SimpleNamespace(url=SimpleNamespace(render_as_string=lambda **kw: 'fake-url'),
                             connect=Mock(return_value=nullcontext(connection)))
    database = SimpleNamespace(get_engine=lambda: engine, metadata=sa.MetaData())
    application = Flask(mode)
    application.extensions['migrate'] = SimpleNamespace(db=database, configure_args={})
    configure = Mock()
    run = Mock()
    monkeypatch.setattr('logging.config.fileConfig', Mock())
    monkeypatch.setattr(context, 'config', configuration, raising=False)
    monkeypatch.setattr(context, 'is_offline_mode', lambda: offline)
    monkeypatch.setattr(context, 'configure', configure)
    monkeypatch.setattr(context, 'begin_transaction', lambda: nullcontext())
    monkeypatch.setattr(context, 'run_migrations', run)
    with application.app_context():
        runpy.run_path(str(ROOT / 'migrations' / 'env.py'))
    configure.assert_called_once()
    run.assert_called_once()
    if offline:
        engine.connect.assert_not_called()
    else:
        engine.connect.assert_called_once_with()
