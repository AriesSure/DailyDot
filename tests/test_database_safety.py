"""Configuration and lifecycle tests without MySQL connections."""

from types import SimpleNamespace
from unittest.mock import Mock

import pymysql
import pytest
import sqlalchemy as sa
from flask import Flask
from sqlalchemy.engine import make_url

from app.config import TestingConfig
from app.extensions import db
from app.factory import create_app, _ensure_dev_tables
from app.testing_database import (
    TestDatabaseSafetyError as SafetyError,
    configure_testing_database,
    engine_url_matches_test_database_url,
    validate_testing_database_config,
)
from tests.database_lifecycle import DatabaseLifecycle, DatabaseLifecycleError, isolated_database
from tests.test_mysql_ci_integration import _close_with_observation


SECRET = 'fake_test_secret_123'
MYSQL = f'mysql+pymysql://dailydot_test:{SECRET}@127.0.0.1:3306/dailydot_test'


def mysql_config():
    return dict(TESTING=True, SQLALCHEMY_DATABASE_URI=MYSQL,
                TEST_DATABASE_PROFILE='github-runner-mysql',
                TEST_DATABASE_ALLOW_MYSQL='1', TEST_DATABASE_GITHUB_ACTIONS='true')


def test_default_and_generic_environment_isolation():
    assert TestingConfig.SQLALCHEMY_DATABASE_URI == 'sqlite:///:memory:'
    config = {'TESTING': True}
    configure_testing_database(config, {'DATABASE_URL': MYSQL})
    assert config['SQLALCHEMY_DATABASE_URI'] == 'sqlite:///:memory:'


@pytest.mark.parametrize('host,profile', [
    ('127.0.0.1', 'github-runner-mysql'), ('mysql', 'github-container-mysql'),
])
def test_both_ci_profiles_are_accepted_without_connecting(host, profile):
    config = mysql_config()
    config['TEST_DATABASE_PROFILE'] = profile
    config['SQLALCHEMY_DATABASE_URI'] = make_url(MYSQL).set(host=host)
    assert validate_testing_database_config(config).host == host


@pytest.mark.parametrize('change', [
    {'SQLALCHEMY_DATABASE_URI': ''},
    {'SQLALCHEMY_DATABASE_URI': 'not-a-url-' + SECRET},
    {'SQLALCHEMY_DATABASE_URI': 'sqlite:///existing.db'},
    {'SQLALCHEMY_DATABASE_URI': 'sqlite://remote/existing.db'},
    {'TEST_DATABASE_PROFILE': 'unknown'},
    {'TEST_DATABASE_ALLOW_MYSQL': ''},
    {'TEST_DATABASE_GITHUB_ACTIONS': ''},
    {'SQLALCHEMY_BINDS': {'other': MYSQL}},
    {'SQLALCHEMY_ENGINE_OPTIONS': {'creator': lambda: None}},
    {'SQLALCHEMY_DATABASE_URI': make_url(MYSQL).set(host='remote.invalid')},
    {'SQLALCHEMY_DATABASE_URI': make_url(MYSQL).set(port=3307)},
    {'SQLALCHEMY_DATABASE_URI': make_url(MYSQL).set(database='production')},
    {'SQLALCHEMY_DATABASE_URI': make_url(MYSQL).set(username='root')},
    {'SQLALCHEMY_DATABASE_URI': make_url(MYSQL).set(drivername='mysql')},
    {'SQLALCHEMY_DATABASE_URI': make_url(MYSQL).set(password='')},
    {'SQLALCHEMY_DATABASE_URI': make_url(MYSQL).update_query_dict({'host': 'remote.invalid'})},
    {'SQLALCHEMY_DATABASE_URI': make_url(MYSQL).set(query={'charset': 'utf8mb4'})},
    {'TESTING': False},
])
def test_invalid_config_rejected_without_leaking(change, capsys, caplog):
    config = mysql_config()
    config.update(change)
    with pytest.raises(SafetyError) as caught:
        validate_testing_database_config(config)
    captured = capsys.readouterr()
    output = str(caught.value) + captured.out + captured.err + caplog.text
    assert SECRET not in output
    assert MYSQL not in output


@pytest.mark.parametrize('query', [{}, {'charset': 'utf8mb4'}])
def test_engine_url_match_accepts_exact_mysql_forms(query):
    configured = validate_testing_database_config(mysql_config())
    assert engine_url_matches_test_database_url(configured.set(query=query), configured)
    assert not configured.query


@pytest.mark.parametrize('change', [
    {'query': {'charset': 'utf8'}},
    {'query': {'charset': 'latin1'}},
    {'query': {'unix_socket': '/tmp/mysql.sock'}},
    {'query': {'unknown': 'value'}},
    {'query': {'charset': 'utf8mb4', 'unknown': 'value'}},
    {'query': {'charset': ('utf8mb4', 'utf8')}},
    {'username': 'other'}, {'password': 'different-fake-password'},
    {'host': 'remote.invalid'}, {'port': 3307}, {'database': 'other'},
    {'drivername': 'mysql+mysqldb'},
])
def test_engine_url_match_rejects_mysql_changes(change):
    configured = validate_testing_database_config(mysql_config())
    assert not engine_url_matches_test_database_url(configured.set(**change), configured)


def test_engine_url_match_rejects_duplicate_charset():
    configured = validate_testing_database_config(mysql_config())
    duplicate = make_url(MYSQL + '?charset=utf8mb4&charset=utf8mb4')
    assert duplicate.query['charset'] == ('utf8mb4', 'utf8mb4')
    assert not engine_url_matches_test_database_url(duplicate, configured)


@pytest.mark.parametrize('configured', [
    make_url(MYSQL).set(query={'charset': 'utf8mb4'}),
    make_url(MYSQL).set(host='remote.invalid'),
    make_url(MYSQL).set(drivername='postgresql'),
    make_url(MYSQL).set(password=''),
    'not-a-url-' + SECRET,
    None,
])
def test_engine_url_match_rejects_untrusted_configuration(configured):
    assert not engine_url_matches_test_database_url(configured, configured)


@pytest.mark.parametrize('candidate,allowed', [
    ('sqlite:///:memory:', True),
    ('sqlite:///existing.db', False),
    ('sqlite:///:memory:?charset=utf8mb4', False),
    ('sqlite:///:memory:?mode=memory', False),
])
def test_engine_url_match_keeps_sqlite_memory_exact(candidate, allowed):
    configured = validate_testing_database_config(dict(TESTING=True, SQLALCHEMY_DATABASE_URI='sqlite:///:memory:'))
    assert engine_url_matches_test_database_url(make_url(candidate), configured) is allowed


def test_flask_sqlalchemy_mysql_engine_default_charset_without_connection(monkeypatch):
    application = Flask(__name__)
    application.config.update(mysql_config())
    configured = validate_testing_database_config(application.config)
    forbidden = Mock(side_effect=AssertionError('engine_creation_must_not_connect'))
    monkeypatch.setattr(sa.engine.Engine, 'connect', forbidden)
    monkeypatch.setattr(sa.engine.Engine, 'raw_connection', forbidden)
    monkeypatch.setattr(pymysql.connections.Connection, 'connect', forbidden)

    db.init_app(application)
    with application.app_context():
        engine = db.engine
        try:
            assert engine.url.query == {'charset': 'utf8mb4'}
            assert validate_testing_database_config(application.config) == configured
            assert not configured.query
            assert engine_url_matches_test_database_url(engine.url, configured)
        finally:
            engine.dispose()
    forbidden.assert_not_called()


def test_factory_reads_test_environment_on_each_call_and_rejects_before_init(monkeypatch):
    import app.factory as factory
    initialize = Mock()
    monkeypatch.setattr(factory, 'init_extensions', initialize)
    # Abort after the initialization seam; no app or database operations follow it.
    initialize.side_effect = RuntimeError('reached_extension_init')
    monkeypatch.setenv('TEST_DATABASE_PROFILE', 'sqlite-memory')
    monkeypatch.delenv('TEST_DATABASE_URL', raising=False)
    monkeypatch.setenv('DATABASE_URL', MYSQL)
    with pytest.raises(RuntimeError, match='reached_extension_init'):
        create_app('testing')
    initialize.assert_called_once()
    assert initialize.call_args.args[0].config['SQLALCHEMY_DATABASE_URI'] == 'sqlite:///:memory:'
    initialize.reset_mock()
    monkeypatch.setenv('TEST_DATABASE_URL', 'sqlite:///existing.db')
    with pytest.raises(SafetyError):
        create_app('testing')
    initialize.assert_not_called()


def test_factory_no_ddl_in_testing(monkeypatch):
    application = Flask(__name__)
    application.testing = True
    create = Mock()
    inspect = Mock()
    monkeypatch.setattr(db, 'create_all', create)
    monkeypatch.setattr(sa, 'inspect', inspect)
    _ensure_dev_tables(application)
    create.assert_not_called()
    inspect.assert_not_called()


class FakeDatabase:
    def __init__(self, url):
        self.engines = {None: SimpleNamespace(url=make_url(url), dispose=Mock())}
        self.session = SimpleNamespace(remove=Mock())
        self.create_all = Mock()
        self.drop_all = Mock()

    @property
    def engine(self):
        return self.engines[None]


@pytest.fixture
def fake_mysql(monkeypatch):
    application = Flask(__name__)
    application.config.update(mysql_config())
    database = FakeDatabase(MYSQL)
    inspector = SimpleNamespace(get_table_names=Mock(return_value=[]),
                                get_view_names=Mock(return_value=[]))
    inspect = Mock(return_value=inspector)
    monkeypatch.setattr('tests.database_lifecycle.sa.inspect', inspect)
    return application, database, inspector, inspect


@pytest.mark.parametrize('query', [{}, {'charset': 'utf8mb4'}])
def test_mysql_success_and_one_time_cleanup(fake_mysql, query):
    application, database, _, inspect = fake_mysql
    engine = database.engine
    engine.url = engine.url.set(query=query)
    with isolated_database(application, database):
        inspect.assert_called_once_with(engine)
        database.create_all.assert_called_once_with(bind_key=None)
        database.drop_all.assert_not_called()
    database.drop_all.assert_called_once_with(bind_key=None)
    database.session.remove.assert_called_once()
    engine.dispose.assert_called_once()


@pytest.mark.parametrize('kind', ['table', 'view', 'create-fails', 'inspect-fails', 'bad-url', 'bad-charset'])
def test_mysql_setup_failure_aborts_without_drop(fake_mysql, kind):
    application, database, inspector, inspect = fake_mysql
    engine = database.engine
    if kind == 'table':
        inspector.get_table_names.return_value = ['existing']
    elif kind == 'view':
        inspector.get_view_names.return_value = ['existing_view']
    elif kind == 'create-fails':
        database.create_all.side_effect = RuntimeError(SECRET)
    elif kind == 'inspect-fails':
        inspect.side_effect = RuntimeError(SECRET)
    elif kind == 'bad-charset':
        engine.url = engine.url.set(query={'charset': 'latin1'})
    else:
        engine.url = make_url(MYSQL).set(host='remote.invalid')
    with pytest.raises(pytest.exit.Exception, match='unsafe_mysql_test_initialization'):
        with isolated_database(application, database):
            pytest.fail('unsafe fixture yielded')
    database.drop_all.assert_not_called()
    engine.dispose.assert_called_once()
    if kind in ('bad-url', 'bad-charset'):
        inspect.assert_not_called()
    if kind != 'create-fails':
        database.create_all.assert_not_called()


@pytest.mark.parametrize('change', ['engine', 'charset'])
def test_mysql_target_change_during_inspection_stops_before_ddl(fake_mysql, change):
    application, database, inspector, inspect = fake_mysql
    engine = database.engine

    def inspect_then_change(current):
        assert current is engine
        if change == 'engine':
            database.engines[None] = SimpleNamespace(url=engine.url, dispose=Mock())
        else:
            engine.url = engine.url.set(query={'charset': 'utf8mb4'})
        return inspector

    inspect.side_effect = inspect_then_change
    with pytest.raises(pytest.exit.Exception, match='unsafe_mysql_test_initialization'):
        with isolated_database(application, database):
            pytest.fail('changed target yielded')
    database.create_all.assert_not_called()
    database.drop_all.assert_not_called()
    database.session.remove.assert_called_once()
    engine.dispose.assert_called_once()


@pytest.mark.parametrize('kind', [
    'config', 'config-password', 'engine', 'url', 'add-charset', 'remove-charset',
    'profile', 'authorization', 'app', 'drop',
])
def test_mysql_cleanup_revalidates_all_ownership(fake_mysql, kind):
    application, database, _, _ = fake_mysql
    engine = database.engine
    if kind == 'remove-charset':
        engine.url = engine.url.set(query={'charset': 'utf8mb4'})
    with application.app_context():
        lifecycle = DatabaseLifecycle(application, database)
        lifecycle.initialize()
        assert lifecycle.url == engine.url
        if kind == 'config':
            application.config['SQLALCHEMY_DATABASE_URI'] = MYSQL.replace('dailydot_test', 'other')
        elif kind == 'config-password':
            application.config['SQLALCHEMY_DATABASE_URI'] = make_url(MYSQL).set(password='changed-fake-password')
        elif kind == 'engine':
            database.engines[None] = SimpleNamespace(url=engine.url, dispose=Mock())
        elif kind == 'url':
            engine.url = engine.url.set(database='other')
        elif kind == 'add-charset':
            engine.url = engine.url.set(query={'charset': 'utf8mb4'})
        elif kind == 'remove-charset':
            engine.url = engine.url.set(query={})
        elif kind == 'profile':
            application.config['TEST_DATABASE_PROFILE'] = 'github-container-mysql'
        elif kind == 'authorization':
            lifecycle.cleanup_authorized = False
        elif kind == 'drop':
            database.drop_all.side_effect = RuntimeError(SECRET)
        if kind == 'app':
            with Flask('other').app_context(), pytest.raises(DatabaseLifecycleError):
                lifecycle.close()
        else:
            with pytest.raises(DatabaseLifecycleError) as caught:
                lifecycle.close()
            assert SECRET not in str(caught.value)
        if kind != 'drop':
            database.drop_all.assert_not_called()
        engine.dispose.assert_called_once()
        lifecycle.close()
        engine.dispose.assert_called_once()


@pytest.mark.parametrize('observation_fails,close_fails', [(False, False), (True, False), (False, True), (True, True)])
def test_close_observation_preserves_release_and_errors(fake_mysql, observation_fails, close_fails):
    _, database, _, _ = fake_mysql
    url = make_url(MYSQL)
    observation_error = DatabaseLifecycleError('observed_target_changed')
    close_error = DatabaseLifecycleError('original_close_failed')
    check = Mock(return_value=(database.engine, url, url),
                 side_effect=observation_error if observation_fails else None)
    lifecycle = SimpleNamespace(initialized=True, cleanup_authorized=False, closed=False,
                                database=database, engine=database.engine, url=url, _check_target=check)

    def release(current):
        current.closed = True
        if close_fails:
            raise close_error

    original_close = Mock(side_effect=release)
    expected_error = close_error if close_fails else observation_error if observation_fails else None
    if expected_error is None:
        _close_with_observation(lifecycle, original_close, url)
    else:
        with pytest.raises(DatabaseLifecycleError) as caught:
            _close_with_observation(lifecycle, original_close, url)
        assert caught.value is expected_error
        if observation_fails and close_fails:
            assert caught.value.__context__ is observation_error
    original_close.assert_called_once_with(lifecycle)
    assert lifecycle.closed
    assert lifecycle.cleanup_authorized is False
    database.drop_all.assert_not_called()


def test_close_observation_releases_after_failed_initialization(fake_mysql):
    application, database, _, inspect = fake_mysql
    engine = database.engine
    engine.url = engine.url.set(query={'charset': 'latin1'})
    lifecycle = DatabaseLifecycle(application, database)
    with application.app_context():
        with pytest.raises(DatabaseLifecycleError, match='initialization_failed'):
            lifecycle.initialize()
        assert lifecycle.engine is None
        assert lifecycle.url is None
        assert lifecycle.initialized is False
        assert lifecycle.cleanup_authorized is False
        _close_with_observation(lifecycle, DatabaseLifecycle.close, make_url(MYSQL))
    assert lifecycle.closed
    inspect.assert_not_called()
    database.session.remove.assert_called_once()
    database.create_all.assert_not_called()
    database.drop_all.assert_not_called()
    engine.dispose.assert_called_once()


def test_mysql_cleanup_failure_aborts_run(fake_mysql):
    application, database, _, _ = fake_mysql
    database.drop_all.side_effect = RuntimeError(SECRET)
    with pytest.raises(pytest.exit.Exception, match='unsafe_mysql_test_cleanup'):
        with isolated_database(application, database):
            pass
    database.engine.dispose.assert_called_once()


def test_sqlite_actual_lifecycle_isolates_and_releases(monkeypatch):
    monkeypatch.setenv('TEST_DATABASE_PROFILE', 'sqlite-memory')
    monkeypatch.delenv('TEST_DATABASE_URL', raising=False)
    drop = Mock(side_effect=AssertionError('sqlite_drop_forbidden'))
    monkeypatch.setattr(db, 'drop_all', drop)
    from app.models import User
    from tests.conftest import app as app_fixture
    for index in range(2):
        fixture = app_fixture.__wrapped__(None)
        application = next(fixture)
        engine = db.engine
        dispose = Mock(wraps=engine.dispose)
        monkeypatch.setattr(engine, 'dispose', dispose)
        remove = Mock(wraps=db.session.remove)
        with monkeypatch.context() as patch:
            patch.setattr(db.session, 'remove', remove)
            try:
                assert sa.inspect(engine).has_table('users')
                assert db.session.query(User).count() == 0
                db.session.add(User(username='isolated', email='isolated@example.com'))
                db.session.commit()
            finally:
                fixture.close()
            assert remove.call_count >= 1
            dispose.assert_called_once()
    drop.assert_not_called()


def test_rejected_config_still_releases_allocated_engine_without_io():
    application = Flask(__name__)
    application.config.update(TESTING=True, TEST_DATABASE_PROFILE='sqlite-memory',
                              SQLALCHEMY_DATABASE_URI='sqlite:///existing.db')
    database = FakeDatabase('sqlite:///:memory:')
    with pytest.raises(DatabaseLifecycleError):
        with isolated_database(application, database):
            pytest.fail('invalid configuration yielded')
    database.create_all.assert_not_called()
    database.drop_all.assert_not_called()
    database.engine.dispose.assert_called_once()


def test_extra_engine_bind_refused_and_all_owned_resources_released(fake_mysql):
    application, database, _, inspect = fake_mysql
    extra = SimpleNamespace(url=make_url('sqlite:///:memory:'), dispose=Mock())
    database.engines['extra'] = extra
    with pytest.raises(pytest.exit.Exception):
        with isolated_database(application, database):
            pytest.fail('extra engine yielded')
    inspect.assert_not_called()
    database.create_all.assert_not_called()
    database.drop_all.assert_not_called()
    extra.dispose.assert_called_once()
    database.engine.dispose.assert_called_once()


def test_sqlite_setup_failure_releases_without_drop(monkeypatch):
    application = Flask(__name__)
    application.config.update(TESTING=True, TEST_DATABASE_PROFILE='sqlite-memory',
                              SQLALCHEMY_DATABASE_URI='sqlite:///:memory:')
    database = FakeDatabase('sqlite:///:memory:')
    database.create_all.side_effect = RuntimeError(SECRET)
    with pytest.raises(DatabaseLifecycleError, match='initialization_failed'):
        with isolated_database(application, database):
            pytest.fail('failed setup yielded')
    database.drop_all.assert_not_called()
    database.session.remove.assert_called_once()
    database.engine.dispose.assert_called_once()


def test_card_service_preserves_original_error_without_ddl(monkeypatch):
    from app.services.card_service import get_card_stack
    application = Flask(__name__)
    application.testing = True
    error = sa.exc.OperationalError('query', {}, RuntimeError('missing_table'))
    query = Mock(side_effect=error)
    create = Mock()
    monkeypatch.setattr(db.session, 'query', query)
    monkeypatch.setattr(db, 'create_all', create)
    with application.app_context(), pytest.raises(sa.exc.OperationalError) as caught:
        get_card_stack(1)
    assert caught.value is error
    query.assert_called_once()
    create.assert_not_called()
