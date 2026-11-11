"""Offline authorization/state-machine tests, never evidence of real MySQL."""

from contextlib import contextmanager
from copy import deepcopy
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch

from flask import Flask
import pymysql
import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from tests import mysql_migration_ci as m


PASSWORD = 'fictional-ci-password-0123456789'
URL = 'mysql+pymysql://dailydot_test:' + PASSWORD + '@127.0.0.1:3306/dailydot_test'
ENV = {'GITHUB_ACTIONS': 'true', 'TEST_DATABASE_PROFILE': 'github-runner-mysql',
       'TEST_DATABASE_ALLOW_MYSQL': '1', 'TEST_DATABASE_URL': URL}


@pytest.mark.parametrize('key,value', [
    ('TEST_DATABASE_PROFILE', 'sqlite-memory'), ('GITHUB_ACTIONS', ''),
    ('TEST_DATABASE_ALLOW_MYSQL', ''),
    ('TEST_DATABASE_URL', URL.replace('127.0.0.1', 'localhost')),
    ('TEST_DATABASE_URL', URL.replace(':3306/', ':3307/')),
    ('TEST_DATABASE_URL', URL.replace('/dailydot_test', '/other')),
    ('TEST_DATABASE_URL', URL.replace('dailydot_test:', 'other:', 1)),
    ('TEST_DATABASE_URL', URL.replace(PASSWORD, '')),
    ('TEST_DATABASE_URL', URL + '?charset=utf8mb4'),
    ('TEST_DATABASE_URL', URL + '?unix_socket=/fake'),
    ('TEST_DATABASE_URL', URL.replace('mysql+pymysql', 'mysql+mysqldb')),
])
def test_admission_rejects_without_engine_or_network(key, value, monkeypatch):
    env = {**ENV, key: value}
    creator = Mock(side_effect=AssertionError('engine must not be created'))
    monkeypatch.setattr(sa, 'create_engine', creator)
    with pytest.raises(RuntimeError):
        m.admit(env)
    creator.assert_not_called()


@contextmanager
def owned(monkeypatch):
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    boundary = m.MigrationBoundary(m.admit())
    with boundary:
        app, database = m.minimal_app(boundary)
        with app.app_context():
            try:
                yield boundary, app, database
            finally:
                database.session.remove()
                # Only the owned pool; no connection has been made by this fixture.
                boundary.pool.dispose()


@pytest.fixture
def owner(monkeypatch):
    backend = Mock(side_effect=AssertionError('offline backend reached'))
    # Installed underneath the child boundary, so all reject paths must stop
    # before these spies. Parent pytest NetworkBoundary is never undone.
    monkeypatch.setattr(pymysql.connections.Connection, 'connect', backend)
    monkeypatch.setattr(socket.socket, 'connect', backend)
    monkeypatch.setattr(socket.socket, 'connect_ex', backend)
    monkeypatch.setattr(socket, 'create_connection', backend)
    with owned(monkeypatch) as value:
        yield (*value, backend)
    backend.assert_not_called()


@pytest.mark.parametrize('mutation', ['engine', 'pool', 'creator', 'invoke', 'url', 'password',
                                      'query', 'binds', 'options', 'testing', 'config_url', 'environment'])
def test_identity_mutations_refuse_before_io(owner, monkeypatch, mutation):
    boundary, app, database, backend = owner
    if mutation == 'engine':
        monkeypatch.setitem(database.engines, None, object())
    elif mutation == 'pool':
        monkeypatch.setattr(boundary.engine, 'pool', object())
    elif mutation == 'creator':
        monkeypatch.setattr(boundary.pool, '_creator', lambda: None)
    elif mutation == 'invoke':
        monkeypatch.setattr(boundary.pool, '_invoke_creator', lambda record: None)
    elif mutation in ('url', 'password', 'query'):
        change = {'database': 'other'} if mutation == 'url' else (
            {'password': 'other'} if mutation == 'password' else {'query': {'charset': 'utf8'}})
        monkeypatch.setattr(boundary.engine, 'url', boundary.engine.url.set(**change))
    elif mutation == 'binds':
        monkeypatch.setitem(app.config, 'SQLALCHEMY_BINDS', {'other': URL})
    elif mutation == 'options':
        monkeypatch.setitem(app.config, 'SQLALCHEMY_ENGINE_OPTIONS', {'creator': lambda: None})
    elif mutation == 'testing':
        monkeypatch.setitem(app.config, 'TESTING', True)
    elif mutation == 'config_url':
        monkeypatch.setitem(app.config, 'SQLALCHEMY_DATABASE_URI', boundary.url.set(password='other'))
    else:
        monkeypatch.setenv('TEST_DATABASE_ALLOW_MYSQL', '')
    with pytest.raises(m.MigrationSafetyError):
        boundary.engine.connect()
    backend.assert_not_called()


def test_other_application_and_other_engine_denied(owner):
    boundary, app, database, backend = owner
    with Flask('other').app_context(), pytest.raises(m.MigrationSafetyError):
        boundary.engine.connect()
    with pytest.raises(m.MigrationSafetyError):
        sa.create_engine(boundary.url)
    with pytest.raises(m.MigrationSafetyError):
        boundary.check(object())
    backend.assert_not_called()


def test_native_pool_recreation_does_not_receive_authority(owner):
    boundary, app, database, backend = owner
    boundary.engine.dispose()
    assert boundary.engine.pool is not boundary.pool
    with pytest.raises(m.MigrationSafetyError):
        boundary.engine.connect()
    backend.assert_not_called()


@pytest.mark.parametrize('operation', ['connect', 'connect_ex', 'udp', 'http', 'dns', 'create',
                                      'creator_direct', 'original_creator_direct', 'raw_driver'])
def test_same_application_has_no_ambient_network_authority(owner, operation):
    boundary, app, database, backend = owner
    with pytest.raises(m.MigrationSafetyError):
        if operation in ('connect', 'connect_ex'):
            with socket.socket() as sock:
                getattr(sock, operation)(('127.0.0.1', 3306))
        elif operation == 'udp':
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.sendto(b'offline', ('127.0.0.1', 3306))
        elif operation == 'http':
            m.requests.get('https://example.invalid')
        elif operation == 'dns':
            socket.getaddrinfo('example.invalid', 443)
        elif operation == 'create':
            socket.create_connection(('127.0.0.1', 3306))
        elif operation == 'creator_direct':
            boundary.authorized_creator(object())
        elif operation == 'original_creator_direct':
            boundary.original_creator()
        else:
            pymysql.connect(host='127.0.0.1', user='dailydot_test', password=PASSWORD,
                            database='dailydot_test', charset='utf8mb4')
    backend.assert_not_called()
    assert m._physical.get() is None and m._driver.get() is None


def stubbed_engine_roundtrip(monkeypatch):
    """Real Engine/Pool/driver wrapper path; simulated transport, no MySQL server."""
    calls = []
    driver_failure = []

    def fake_tcp(sock, address):
        calls.append(address)

    def fake_create(address, timeout):
        info = socket.getaddrinfo(*address, 0, socket.SOCK_STREAM)[0]
        with socket.socket(info[0], info[1], info[2]) as sock:
            sock.connect(info[4])
        return object()

    def fake_driver(connection, sock=None):
        assert m._physical.get() is m._driver.get()
        if driver_failure:
            raise driver_failure[0]
        socket.create_connection((connection.host, connection.port), connection.connect_timeout)

    class Cursor:
        def execute(self, *args):
            pass
        def close(self):
            pass

    monkeypatch.setattr(socket.socket, 'connect', fake_tcp)
    monkeypatch.setattr(socket, 'create_connection', fake_create)
    monkeypatch.setattr(pymysql.connections.Connection, 'connect', fake_driver)
    monkeypatch.setattr(pymysql.connections.Connection, 'cursor', lambda self: Cursor())
    for name in ('rollback', 'commit', 'close'):
        monkeypatch.setattr(pymysql.connections.Connection, name, lambda self: None)
    with owned(monkeypatch) as (boundary, app, database):
        monkeypatch.setattr(boundary.engine.dialect, 'initialize', lambda conn: None)
        first = boundary.engine.connect()
        first.invalidate()
        first.close()
        second = boundary.engine.connect()
        second.invalidate()
        second.close()
        assert calls == [('127.0.0.1', 3306)] * 2
        assert boundary.physical_connections == 2
        assert boundary.pool._creator is boundary.original_creator
        assert boundary.pool._invoke_creator is boundary.authorized_creator
        assert m._physical.get() is None and m._driver.get() is None and m._engine_lease.get() is None
        driver_failure.append(RuntimeError('synthetic driver failure'))
        with pytest.raises(RuntimeError) as raised:
            boundary.engine.connect()
        assert raised.value is driver_failure[0]
        assert m._physical.get() is None and m._driver.get() is None and m._engine_lease.get() is None
        failure = RuntimeError('synthetic creator failure')
        monkeypatch.setattr(boundary, 'original_invoke', Mock(side_effect=failure))
        with pytest.raises(RuntimeError) as raised:
            boundary.engine.connect()
        assert raised.value is failure
        assert m._physical.get() is None and m._driver.get() is None and m._engine_lease.get() is None


def test_real_engine_pool_path_issues_and_resets_permits_with_stub_transport():
    # No parent monkeypatch is undone: use a clean, independently guarded child.
    # Every transport call is stubbed before its boundary is installed.
    code = ('import sys; sys.path.insert(0,' + repr(str(m.ROOT)) + '); '
            'import pytest; from tests.test_mysql_migration_safety import stubbed_engine_roundtrip; '
            'p=pytest.MonkeyPatch(); '
            '\ntry: stubbed_engine_roundtrip(p)\nfinally: p.undo()')
    result = subprocess.run([sys.executable, '-I', '-B', '-c', code], cwd=m.ROOT,
                            env={k: os.environ[k] for k in m.SYSTEM_ENV if k in os.environ},
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_direct_pool_checkout_cannot_issue_permit(owner):
    boundary, app, database, backend = owner
    with pytest.raises(m.MigrationSafetyError):
        boundary.pool.connect()
    assert m._physical.get() is None and m._driver.get() is None
    backend.assert_not_called()


def expected_schema(metadata):
    result = {}
    for table in metadata.tables.values():
        indexes = {(i.name, bool(i.unique), tuple(c.name for c in i.columns)) for i in table.indexes}
        indexes.update((c.name, True, tuple(x.name for x in c.columns)) for c in table.constraints
                       if isinstance(c, sa.UniqueConstraint))
        columns = []
        for c in table.c:
            kind = 'TINYINT(1)' if isinstance(c.type, sa.Boolean) else str(c.type.compile(dialect=mysql.dialect()))
            columns.append((c.name, kind, c.nullable, None, c.name == 'id', False))
        result[table.name] = {'columns': tuple(columns), 'pk': ('id',), 'indexes': tuple(sorted(indexes)),
            'unique': tuple(sorted((name, cols) for name, unique, cols in indexes if unique)),
            'foreign': tuple(sorted((tuple(f.parent.name for f in c.elements), c.elements[0].column.table.name,
                                    tuple(f.column.name for f in c.elements)) for c in table.foreign_key_constraints)),
            'foreign_names': tuple(table.name + '_ibfk_' + str(i + 1) for i, _ in enumerate(table.foreign_key_constraints)),
            'collation': 'utf8mb4_0900_ai_ci', 'charset': 'utf8mb4', 'engine': 'InnoDB'}
    result['alembic_version'] = {'columns': (('version_num', 'VARCHAR(32)', False, None, False, False),),
        'pk': ('version_num',), 'indexes': (), 'unique': (), 'foreign': (), 'foreign_names': (),
        'collation': 'utf8mb4_0900_ai_ci', 'charset': 'utf8mb4', 'engine': 'InnoDB'}
    return result


def fixture_grants():
    from tests.mysql_ci import REQUIRED_PRIVILEGES, validate_grants
    grants = ["GRANT USAGE ON *.* TO 'dailydot_test'@'%'",
              'GRANT ' + ', '.join(sorted(REQUIRED_PRIVILEGES))
              + " ON `dailydot\\_test`.* TO 'dailydot_test'@'%'"]
    return grants, validate_grants(grants)


@pytest.fixture
def catalog(monkeypatch):
    """Pure in-memory catalog adapter. It does not emulate successful MySQL migration."""
    from app.extensions import db
    from tests import mysql_ci
    catalog = SimpleNamespace(schema=expected_schema(db.metadata), drops=[], fail_at=None, after_drop=None,
                              on_begin=None, inspections=[], pinned_identity=None, sql=[])
    catalog.identity = ('dailydot_test', 'dailydot_test@%', '8.0.46', fixture_grants()[1])
    catalog.grants = fixture_grants()[0]
    catalog.real_inspect_server = mysql_ci.inspect_server
    catalog.data = {name: () for name in m.TABLES}
    catalog.data['alembic_version'] = ((m.HEAD,),)

    class Engine:
        closed = invalidated = False

        @property
        def engine(self):
            return self

        def in_transaction(self):
            return True

        @contextmanager
        def connect(self):
            yield self

        @contextmanager
        def begin(self):
            if catalog.on_begin:
                catalog.on_begin(self)
            yield self
        def execute(self, statement):
            sql = str(statement)
            catalog.sql.append((self, sql))
            if sql.startswith('SELECT DATABASE()'):
                return SimpleNamespace(one=lambda: (*catalog.identity[:3], 0))
            if sql == 'SHOW GRANTS FOR CURRENT_USER()':
                return SimpleNamespace(scalars=lambda: list(catalog.grants))
            if sql == 'SHOW DATABASES':
                return SimpleNamespace(scalars=lambda: ['dailydot_test', 'information_schema'])
            assert sql in m.DROP_SQL.values()
            name = next(key for key, value in m.DROP_SQL.items() if value == sql)
            if len(catalog.drops) == catalog.fail_at:
                raise RuntimeError('synthetic cleanup failure')
            catalog.drops.append(name)
            del catalog.schema[name]
            if catalog.after_drop:
                catalog.after_drop(name)

    run = m.MigrationRun()
    run.state = 'TARGET_VALIDATED'
    run.db = db
    run.boundary = SimpleNamespace(engine=Engine(), check=Mock(return_value=None), attempts=0)
    run.collation = 'utf8mb4_0900_ai_ci'
    run.verified_schema, run.verified_data = deepcopy(catalog.schema), deepcopy(catalog.data)
    def inspect(source, require_empty=False):
        catalog.inspections.append(source)
        if isinstance(source, m._ConnectionIdentitySource) and catalog.pinned_identity is not None:
            return catalog.pinned_identity
        return catalog.identity
    monkeypatch.setattr(mysql_ci, 'inspect_server', inspect)
    monkeypatch.setattr(m, 'schema_snapshot', lambda engine: deepcopy(catalog.schema))
    monkeypatch.setattr(m, 'data_snapshot', lambda engine: deepcopy(catalog.data))
    run.verify_server_identity(run.boundary.engine)
    run.state = 'DATA_AND_DOWNGRADE_VERIFIED'
    return run, catalog


@pytest.mark.parametrize('state', m.STATES[:5] + ('CLEANUP_AUTHORIZED', 'EMPTY_VERIFIED', 'DISPOSED'))
def test_unverified_state_never_gets_cleanup(catalog, state):
    run, data = catalog
    run.state = state
    with pytest.raises(m.MigrationSafetyError):
        run.authorize_cleanup()
    assert not run.cleanup_authorized and data.drops == []


@pytest.mark.parametrize('mutation', ['initial', 'mixed', 'unknown', 'revision', 'data', 'identity', 'duplicate_fk'])
def test_cleanup_requires_complete_current_schema_and_proof(catalog, mutation):
    run, data = catalog
    if mutation == 'initial':
        del data.schema['cards']
    elif mutation == 'mixed':
        columns = list(data.schema['todos']['columns'])
        index = next(i for i, c in enumerate(columns) if c[0] == 'completed')
        columns[index] = ('completed', 'TINYINT(1)', True, None, False, False)
        data.schema['todos']['columns'] = tuple(columns)
    elif mutation == 'unknown':
        data.schema['unknown'] = {}
    elif mutation == 'revision':
        data.data['alembic_version'] = ((m.INITIAL,),)
    elif mutation == 'data':
        data.data['users'] = (('changed',),)
    elif mutation == 'duplicate_fk':
        data.schema['cards']['foreign'] *= 2
        data.schema['cards']['foreign_names'] += ('extra_fk',)
    else:
        run.boundary.check.side_effect = m.MigrationSafetyError('migration_engine_changed')
    with pytest.raises(m.MigrationSafetyError):
        run.authorize_cleanup()
    assert not run.cleanup_authorized and data.drops == []


def test_cleanup_uses_real_fk_graph_and_checks_remaining_inventory(catalog):
    run, data = catalog
    order = m.cleanup_order(data.schema)
    assert order.index('records') < order.index('habits') < order.index('users')
    assert order.index('todos') < order.index('users') and order.index('cards') < order.index('users')
    run.authorize_cleanup()
    run.cleanup()
    assert data.drops == order and not data.schema
    assert run.state == 'EMPTY_VERIFIED' and not run.cleanup_authorized


@pytest.mark.parametrize('failure_index', [0, 2, 5])
def test_cleanup_failure_stops_all_subsequent_ddl(catalog, failure_index):
    run, data = catalog
    run.authorize_cleanup()
    data.fail_at = failure_index
    with pytest.raises(RuntimeError, match='synthetic cleanup failure'):
        run.cleanup()
    assert len(data.drops) == failure_index and not run.cleanup_authorized


def test_inventory_change_after_authorization_never_drops(catalog):
    run, data = catalog
    run.authorize_cleanup()
    data.schema['unknown'] = {}
    with pytest.raises(m.MigrationSafetyError):
        run.cleanup()
    assert data.drops == [] and not run.cleanup_authorized


def test_unknown_object_during_cleanup_stops_after_current_drop(catalog):
    run, data = catalog
    run.authorize_cleanup()
    data.after_drop = lambda name: data.schema.update({'unknown': {}})
    with pytest.raises(m.MigrationSafetyError):
        run.cleanup()
    assert len(data.drops) == 1 and 'unknown' in data.schema and not run.cleanup_authorized


@pytest.mark.parametrize('state', ['UNAUTHORIZED', 'INITIAL_VERIFIED', 'HEAD_VERIFIED',
                                   'DATA_AND_DOWNGRADE_VERIFIED'])
def test_cleanup_flag_alone_is_not_authority(catalog, state):
    run, data = catalog
    run.state = state
    run.cleanup_authorized = True
    with pytest.raises(m.MigrationSafetyError):
        run.cleanup()
    assert not run.cleanup_authorized and data.drops == []


def test_minimal_app_has_no_factory_blueprints_or_schema_side_effects(owner, monkeypatch):
    boundary, app, database, backend = owner
    assert not app.testing and not app.blueprints
    assert set(database.engines) == {None}
    assert boundary.physical_connections == 0
    backend.assert_not_called()


def test_release_errors_never_mask_primary_exception():
    run = m.MigrationRun()
    app = Flask('release')
    run.app = app
    run.db = SimpleNamespace(session=SimpleNamespace(remove=Mock(side_effect=RuntimeError('release failure'))))
    pool = SimpleNamespace(dispose=Mock(side_effect=RuntimeError('pool failure')))
    run.boundary = SimpleNamespace(engine=SimpleNamespace(pool=object()), pool=pool)
    primary = ValueError('original failure')
    with pytest.raises(ValueError) as result:
        try:
            raise primary
        finally:
            run.release(primary)
    assert result.value is primary
    run.db.session.remove.assert_called_once()
    pool.dispose.assert_called_once()
    with pytest.raises(m.MigrationSafetyError, match='migration_resource_release_failed'):
        run.release()


def test_safe_result_and_launch_never_display_raw_child_output(monkeypatch, capsys):
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    for name in ('DATABASE_URL', 'ROOT_PASSWORD', 'MYSQL_PWD', 'SECRET_KEY', 'FLASK_APP', 'PYTHONPATH'):
        monkeypatch.setenv(name, 'fictional-private-value')
    error = sa.exc.OperationalError(URL, {'secret': PASSWORD}, RuntimeError(PASSWORD + URL))
    result = m.safe_failure(error, m.MigrationRun())
    assert PASSWORD not in json.dumps(result) and URL not in json.dumps(result)
    child = Mock(return_value=SimpleNamespace(returncode=1, stdout=json.dumps(result), stderr=URL + PASSWORD))
    monkeypatch.setattr(m.subprocess, 'run', child)
    assert m.launch() == result
    args, kwargs = child.call_args
    assert args[0] == [m.sys.executable, '-I', '-B', str(Path(m.__file__).resolve())]
    assert set(kwargs['env']) <= set(m.SYSTEM_ENV + m.CI_ENV)
    assert kwargs['capture_output'] and kwargs['timeout'] == 240
    assert PASSWORD not in ' '.join(args[0]) and not capsys.readouterr().err


@pytest.mark.parametrize('bad', ['raw-stderr-is-not-json', {'status': URL}, {'password': PASSWORD}])
def test_invalid_child_protocol_exports_no_credentials(monkeypatch, bad):
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    output = bad if isinstance(bad, str) else json.dumps(bad)
    monkeypatch.setattr(m.subprocess, 'run', Mock(return_value=SimpleNamespace(returncode=1, stdout=output, stderr=PASSWORD)))
    with pytest.raises(m.MigrationSafetyError) as result:
        m.launch()
    assert PASSWORD not in str(result.value) and URL not in str(result.value)


def test_actual_child_refuses_local_profile_with_only_structured_output():
    env = {key: os.environ[key] for key in m.SYSTEM_ENV if key in os.environ}
    env['TEST_DATABASE_PROFILE'] = 'sqlite-memory'
    result = subprocess.run([sys.executable, '-I', '-B', str(Path(m.__file__).resolve())],
                            cwd=m.ROOT, env=env, capture_output=True, text=True, timeout=30)
    payload = m.validate_result(json.loads(result.stdout))
    assert result.returncode == 1 and payload['status'] == 'failed'
    assert payload['stage'] == 'admission' and payload['state'] == 'UNAUTHORIZED'


def test_preflight_failure_has_safe_distinct_fixed_code():
    run = m.MigrationRun()
    run.stage = 'forward'
    payload = m.safe_failure(RuntimeError('forward_migration_preflight_failed'), run)
    assert m.validate_result(payload)['error_code'] == 'forward_migration_preflight_failed'


def test_success_protocol_accepts_only_bounded_verified_facts():
    payload = {'status': 'passed', 'stage': 'complete', 'substage': None, 'state': 'DISPOSED', 'error_code': None,
               'exception_type': None, 'mysql_errno': None,
               'facts': {'server_version': '8.0.46', 'charset': 'utf8mb4', 'collation': 'utf8mb4_0900_ai_ci',
                         'foreign_keys': True, 'autocommit': False, 'database': 'dailydot_test',
                         'account': 'dailydot_test@%',
                         'tables': {name: {'charset': 'utf8mb4', 'collation': 'utf8mb4_0900_ai_ci'} for name in m.TABLES},
                         'types': {'record_date': 'DATE', 'record_time': 'TIME', 'todo_completed': 'TINYINT(1)'}}}
    assert m.validate_result(payload) == payload
    payload['facts']['password'] = PASSWORD
    with pytest.raises(m.MigrationSafetyError):
        m.validate_result(payload)


def test_timeout_does_not_export_captured_stderr(monkeypatch):
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(m.subprocess, 'run', Mock(side_effect=subprocess.TimeoutExpired('fixed-script', 240,
                                                                                     stderr=PASSWORD)))
    with pytest.raises(m.MigrationSafetyError, match='migration_child_timeout') as result:
        m.launch()
    assert PASSWORD not in str(result.value)


def test_parent_boundary_module_is_not_modified_or_disabled():
    import importlib
    from tests.network_boundary import NetworkBoundary
    before = (NetworkBoundary.engine_allowed, NetworkBoundary.install, socket.socket.connect,
              socket.create_connection, sa.engine.Engine.connect)
    importlib.reload(m)
    run = m.MigrationRun()
    assert before == (NetworkBoundary.engine_allowed, NetworkBoundary.install, socket.socket.connect,
                      socket.create_connection, sa.engine.Engine.connect)
    assert run.state == 'UNAUTHORIZED' and not run.cleanup_authorized
    source = Path(m.__file__).read_text(encoding='utf8')
    assert 'NetworkBoundary' not in source and '.undo(' not in source


def changed_identity(identity, field):
    values = list(deepcopy(identity))
    replacements = {'database': (0, 'other_database'), 'account': (1, 'root@localhost'),
                    'version': (2, '8.0.47'), 'scopes': (3, ['unapproved scope'])}
    index, value = replacements[field]
    values[index] = value
    return tuple(values)


@pytest.mark.parametrize('field', ['database', 'account', 'version', 'scopes'])
def test_initial_identity_bad_helper_return_is_rejected(monkeypatch, field):
    from tests import mysql_ci
    good = ('dailydot_test', 'dailydot_test@%', '8.0.46', fixture_grants()[1])
    bad = changed_identity(good, field)
    if field == 'version':
        bad = (*bad[:2], '5.7.44', bad[3])
    run = m.MigrationRun()
    run.state = 'TARGET_VALIDATED'
    run.cleanup_authorized = True
    backend = Mock(side_effect=AssertionError('initial identity rejection must not open or write a database'))
    run.boundary = SimpleNamespace(engine=SimpleNamespace(connect=backend, begin=backend, execute=backend), check=Mock())
    helper = Mock(return_value=bad)
    monkeypatch.setattr(mysql_ci, 'inspect_server', helper)
    with pytest.raises(m.MigrationSafetyError, match='^migration_server_identity_changed$'):
        run.verify_server_identity(run.boundary.engine)
    helper.assert_called_once()
    assert run.server_identity is None and not run.cleanup_authorized
    backend.assert_not_called()


@pytest.mark.parametrize('field', ['database', 'account', 'version', 'scopes'])
def test_post_migration_identity_change_revokes_cleanup_without_ddl(catalog, field):
    run, data = catalog
    run.state = 'HEAD_VERIFIED'
    run.cleanup_authorized = True
    data.identity = changed_identity(data.identity, field)
    data.schema['unknown_fixture_object'] = {'sentinel': True}
    before_schema = deepcopy(data.schema)
    before = len(data.inspections)
    with pytest.raises(m.MigrationSafetyError, match='^migration_server_identity_changed$'):
        run.verify_server_identity(run.boundary.engine)
    assert len(data.inspections) == before + 1
    assert data.drops == [] and not run.cleanup_authorized and data.schema == before_schema


@pytest.mark.parametrize('field', ['database', 'account', 'version', 'scopes'])
def test_identity_change_after_cleanup_authorization_prevents_all_drops(catalog, field):
    run, data = catalog
    run.authorize_cleanup()
    data.identity = changed_identity(data.identity, field)
    before = deepcopy(data.schema)
    with pytest.raises(m.MigrationSafetyError, match='^migration_server_identity_changed$'):
        run.cleanup()
    assert not run.cleanup_authorized and data.drops == [] and data.schema == before


@pytest.mark.parametrize('field', ['database', 'account', 'version', 'scopes'])
def test_drop_connection_identity_is_checked_not_another_pool_session(catalog, field):
    run, data = catalog
    run.authorize_cleanup()
    # Engine-level helper calls remain valid. Only the DROP connection's return
    # changes, exposing any implementation that checks a different connection.
    data.pinned_identity = changed_identity(data.identity, field)
    before = deepcopy(data.schema)
    with pytest.raises(m.MigrationSafetyError, match='^migration_server_identity_changed$'):
        run.cleanup()
    assert isinstance(data.inspections[-1], m._ConnectionIdentitySource)
    assert data.inspections[-1].connection is run.boundary.engine
    assert not run.cleanup_authorized and data.drops == [] and data.schema == before


def test_scope_snapshot_uses_existing_policy_and_is_immutable(catalog):
    run, data = catalog
    snapshot = run.server_identity
    assert isinstance(snapshot, tuple) and isinstance(snapshot[3], tuple)
    # Row and privilege ordering do not change the authorization range.
    usage, database_scope = data.identity[3]
    privileges, scope = database_scope.split(' ON ', 1)
    data.identity = (*data.identity[:3], [', '.join(reversed(privileges.split(', '))) + ' ON ' + scope, usage])
    assert run.verify_server_identity(run.boundary.engine) == snapshot
    # Dropping even an optional, otherwise policy-valid scope changes baseline.
    data.identity = (*data.identity[:3], [database_scope])
    run.cleanup_authorized = True
    with pytest.raises(m.MigrationSafetyError, match='^migration_server_identity_changed$'):
        run.verify_server_identity(run.boundary.engine)
    assert run.server_identity == snapshot and not run.cleanup_authorized and data.drops == []


def test_real_identity_helper_reads_same_transaction_before_every_drop(catalog, monkeypatch):
    from tests import mysql_ci
    run, data = catalog
    pinned = []

    def inspect(source, require_empty=False):
        if isinstance(source, m._ConnectionIdentitySource):
            pinned.append(source.connection)
        return data.real_inspect_server(source, require_empty=require_empty)

    monkeypatch.setattr(mysql_ci, 'inspect_server', inspect)
    run.authorize_cleanup()
    run.cleanup()
    assert len(pinned) == len(m.TABLES) and all(conn is run.boundary.engine for conn in pinned)
    for index, (connection, sql) in enumerate(data.sql):
        if sql.startswith('DROP TABLE'):
            reads = data.sql[index - 3:index]
            assert all(conn is connection for conn, statement in reads)
            assert reads[0][1].startswith('SELECT DATABASE(), CURRENT_USER(), VERSION()')
            assert [statement for conn, statement in reads[1:]] == ['SHOW GRANTS FOR CURRENT_USER()', 'SHOW DATABASES']


@pytest.mark.parametrize('field', ['database', 'account', 'version'])
def test_real_helper_rejects_identity_change_inside_first_drop_transaction(catalog, monkeypatch, field):
    from tests import mysql_ci
    run, data = catalog
    monkeypatch.setattr(mysql_ci, 'inspect_server', data.real_inspect_server)
    run.authorize_cleanup()
    data.on_begin = lambda connection: setattr(data, 'identity', changed_identity(data.identity, field))
    before = deepcopy(data.schema)
    with pytest.raises(m.MigrationSafetyError, match='^migration_server_identity_changed$'):
        run.cleanup()
    assert data.drops == [] and data.schema == before and not run.cleanup_authorized


def test_real_grant_validator_rejects_changed_raw_grants_before_drop(catalog, monkeypatch):
    from tests import mysql_ci
    run, data = catalog
    monkeypatch.setattr(mysql_ci, 'inspect_server', data.real_inspect_server)
    run.authorize_cleanup()
    data.on_begin = lambda connection: data.grants.append("GRANT SELECT ON *.* TO 'dailydot_test'@'%'")
    with pytest.raises(m.MigrationSafetyError, match='^migration_server_identity_changed$'):
        run.cleanup()
    assert data.drops == [] and not run.cleanup_authorized


def test_identity_change_after_one_drop_stops_remaining_drops(catalog):
    run, data = catalog
    run.authorize_cleanup()
    data.after_drop = lambda name: setattr(data, 'identity', changed_identity(data.identity, 'account'))
    with pytest.raises(m.MigrationSafetyError, match='^migration_server_identity_changed$'):
        run.cleanup()
    assert data.drops == ['records'] and not run.cleanup_authorized
    assert set(data.schema) == m.TABLES - {'records'}  # No restore or additional DDL.


def test_missing_initial_snapshot_cannot_be_adopted_during_cleanup(catalog):
    run, data = catalog
    run.server_identity = None
    with pytest.raises(m.MigrationSafetyError, match='^migration_server_identity_changed$'):
        run.authorize_cleanup()
    assert run.server_identity is None and data.drops == [] and not run.cleanup_authorized


def test_identity_failure_json_contains_only_fixed_classification():
    run = m.MigrationRun()
    result = m.safe_failure(m.MigrationSafetyError('migration_server_identity_changed'), run)
    assert m.validate_result(result)['error_code'] == 'migration_server_identity_changed'
    assert result['facts'] == {} and URL not in json.dumps(result) and PASSWORD not in json.dumps(result)


# Diagnostics below use DBAPI exception objects and a Session double, not MySQL.
CRUD_DIRECT_CODES = (
    'migration_orm_default_failed', 'migration_orm_update_failed', 'migration_orm_rollback_failed',
    'migration_constraint_wrong_error', 'migration_constraint_not_enforced',
    'migration_orm_delete_failed', 'migration_auto_increment_failed',
)
DIAGNOSTIC_STEPS = (
    'insert_defaults_commit', 'verify_defaults', 'update_commit', 'verify_update',
    'rollback_probe', 'record_unique', 'card_unique', 'card_foreign_key', 'todo_duplicate',
    'card_delete', 'card_survivor', 'session_remove', 'schema_snapshot', 'data_snapshot',
)


def diagnostic_run():
    run = m.MigrationRun()
    run.stage, run.substage, run.state = 'crud', 'verify_defaults', 'HEAD_VERIFIED'
    # Failure output must not serialize facts gathered earlier in the run.
    run.facts = {'password': PASSWORD, 'database_url': URL}
    return run


def driver_error(errno, exception_class=sa.exc.IntegrityError):
    return exception_class('fictional SQL ' + URL, {'password': PASSWORD},
                           pymysql.IntegrityError(errno, 'fictional private message ' + PASSWORD))


@pytest.mark.parametrize('code', CRUD_DIRECT_CODES)
def test_crud_direct_codes_are_preserved_without_raw_facts(code):
    result = m.safe_failure(m.MigrationSafetyError(code), diagnostic_run())
    assert m.validate_result(result)['error_code'] == code
    assert result['facts'] == {} and result['mysql_errno'] is None
    assert PASSWORD not in json.dumps(result) and URL not in json.dumps(result)


@pytest.mark.parametrize('code', [
    'migration_environment_changed', 'migration_application_changed', 'migration_config_changed',
    'migration_engine_changed', 'migration_target_changed', 'migration_creator_changed',
    'migration_physical_path_invalid', 'migration_nested_creator', 'migration_driver_path_invalid',
    'migration_engine_creation_denied', 'migration_driver_denied', 'migration_driver_target_changed',
    'migration_tcp_denied', 'migration_socket_options_denied', 'migration_dns_denied', 'migration_io_denied',
    'migration_unknown_schema_object', 'migration_partition_forbidden', 'migration_unknown_table',
    'migration_index_options_changed', 'migration_foreign_schema', 'migration_foreign_options',
    'migration_unknown_check',
])
def test_crud_boundary_and_snapshot_codes_are_preserved(code):
    payload = m.safe_failure(m.MigrationSafetyError(code), diagnostic_run())
    assert m.validate_result(payload)['error_code'] == code
    run = diagnostic_run()
    run.stage = 'initial'
    assert m.safe_failure(m.MigrationSafetyError(code), run)['error_code'] == 'mysql_forward_migration_failed'


@pytest.mark.parametrize('code', ['unknown_fixed_code', URL + PASSWORD,
                                 'migration_bind_invalid', 'migration_resource_release_failed'])
def test_unapproved_codes_do_not_escape(code):
    result = m.safe_failure(m.MigrationSafetyError(code), diagnostic_run())
    assert m.validate_result(result)['error_code'] == 'mysql_forward_migration_failed'
    assert PASSWORD not in json.dumps(result) and URL not in json.dumps(result)


@pytest.mark.parametrize('substage', DIAGNOSTIC_STEPS)
def test_fixed_substages_survive_serialization(substage):
    run = diagnostic_run()
    run.substage = substage
    result = m.safe_failure(m.MigrationSafetyError('migration_orm_update_failed'), run)
    assert m.validate_result(result)['substage'] == substage


@pytest.mark.parametrize('value', [1062, 1452, 1, 65535])
@pytest.mark.parametrize('exception_class', [sa.exc.IntegrityError, sa.exc.OperationalError, sa.exc.DBAPIError])
def test_public_dbapi_integer_errno_is_preserved(value, exception_class):
    result = m.safe_failure(driver_error(value, exception_class), diagnostic_run())
    assert m.validate_result(result)['mysql_errno'] == value
    assert PASSWORD not in json.dumps(result) and 'fictional SQL' not in json.dumps(result)


@pytest.mark.parametrize('value', ['1062', True, False, 0, -1, 65536, 1062.0, None, PASSWORD + URL])
def test_invalid_driver_errno_is_null_without_parsing_text(value):
    result = m.safe_failure(driver_error(value), diagnostic_run())
    assert m.validate_result(result)['mysql_errno'] is None
    assert PASSWORD not in json.dumps(result) and URL not in json.dumps(result)


@pytest.mark.parametrize('kind', ['ordinary', 'duck_orig', 'wrong_dbapi', 'no_errno'])
def test_no_recognizable_mysql_dbapi_error_means_no_errno(kind):
    error = RuntimeError('1062 ' + PASSWORD + URL)
    if kind == 'duck_orig':
        error.orig = pymysql.IntegrityError(1062, PASSWORD)
    elif kind == 'wrong_dbapi':
        error = sa.exc.IntegrityError(URL, {'secret': PASSWORD}, RuntimeError(1062, PASSWORD))
    elif kind == 'no_errno':
        error = sa.exc.DBAPIError(URL, None, pymysql.MySQLError())
    payload = m.safe_failure(error, diagnostic_run())
    assert m.validate_result(payload)['mysql_errno'] is None
    assert PASSWORD not in json.dumps(payload) and URL not in json.dumps(payload)


@pytest.mark.parametrize('field,value', [
    ('stage', 'unapproved'), ('stage', []), ('stage', None),
    ('substage', 'unapproved'), ('substage', {}), ('substage', False),
    ('error_code', 'unapproved'), ('error_code', URL), ('error_code', []), ('error_code', None),
    ('exception_type', 'PrivateDriverError'), ('exception_type', {}), ('exception_type', None),
    ('mysql_errno', True), ('mysql_errno', False), ('mysql_errno', '1062'), ('mysql_errno', 1062.0),
    ('mysql_errno', 0), ('mysql_errno', -1), ('mysql_errno', 65536), ('mysql_errno', []),
    ('state', 'unapproved'), ('state', []), ('facts', {'password': PASSWORD}), ('facts', []),
    ('status', True), ('status', {}), ('extra', PASSWORD),
])
def test_failure_protocol_rejects_unapproved_fields_and_types(field, value):
    payload = m.safe_failure(m.MigrationSafetyError('migration_orm_default_failed'), diagnostic_run())
    payload[field] = value
    with pytest.raises(m.MigrationSafetyError, match='^migration_child_protocol_invalid$'):
        m.validate_result(payload)


def test_substage_and_crud_error_cannot_be_reused_in_another_stage():
    payload = m.safe_failure(m.MigrationSafetyError('migration_orm_default_failed'), diagnostic_run())
    payload['stage'] = 'repeat'
    with pytest.raises(m.MigrationSafetyError, match='^migration_child_protocol_invalid$'):
        m.validate_result(payload)
    payload['substage'] = None
    with pytest.raises(m.MigrationSafetyError, match='^migration_child_protocol_invalid$'):
        m.validate_result(payload)


def test_unknown_runtime_diagnostics_are_filtered_before_json():
    run = diagnostic_run()
    run.stage, run.substage, run.state = URL, PASSWORD, URL
    payload = m.safe_failure(ValueError(PASSWORD + URL), run)
    assert m.validate_result(payload)['stage'] == 'admission'
    assert payload['substage'] is None and payload['state'] == 'UNAUTHORIZED'
    assert payload['exception_type'] == 'OtherError'
    assert PASSWORD not in json.dumps(payload) and URL not in json.dumps(payload)


@pytest.fixture
def crud_double(monkeypatch):
    """Run the real CRUD/diagnostic code with an in-memory Session DOUBLE.

    No SQL execution, network or ORM/database semantics are claimed here.
    Errors can be injected at the real operation boundary after each marker.
    """
    from app import models
    run = diagnostic_run()
    run.app = Flask('offline-crud-diagnostics')
    operations = []
    control = SimpleNamespace(fail_at=None, primary=None, wrong_errno=None, missing_constraint=False)

    def touch(operation):
        operations.append((run.substage, operation))
        if run.substage == control.fail_at:
            raise control.primary

    class ModelDouble:
        def __init__(self, **values):
            self.__dict__.update(id=0, checkin_time=None, completed=False, streak=0, habits_completed=0)
            self.__dict__.update(values)

        def __getattribute__(self, name):
            if name == 'checkin_time' and run.substage == 'verify_defaults':
                touch('read_default')
            return object.__getattribute__(self, name)

    class RecordDouble(ModelDouble):
        pass

    class TodoDouble(ModelDouble):
        pass

    class CardDouble(ModelDouble):
        pass

    for name, replacement in [('Record', RecordDouble), ('Todo', TodoDouble), ('Card', CardDouble)]:
        monkeypatch.setattr(models, name, replacement)

    class SessionDouble:
        def __init__(self):
            self.first = {}
            self.next_id = 100

        def add_all(self, objects):
            touch('add_all')
            for item in objects:
                item.id = self.next_id
                self.next_id += 1
                self.first[type(item)] = item

        def add(self, item):
            touch('add')
            item.id = self.next_id
            self.next_id += 1

        def commit(self):
            touch('commit')
            if run.substage in ('record_unique', 'card_unique', 'card_foreign_key'):
                if not control.missing_constraint:
                    expected = 1452 if run.substage == 'card_foreign_key' else 1062
                    raise driver_error(control.wrong_errno if control.wrong_errno is not None else expected)
            elif run.substage == 'insert_defaults_commit':
                self.first[RecordDouble].checkin_time = m.datetime(2025, 1, 2, 12).time()

        def get(self, model, identity):
            touch('get')
            return self.first[model]

        def query(self, model):
            touch('query')
            return SimpleNamespace(count=lambda: 1 if run.substage == 'rollback_probe' else 0)

        def delete(self, item):
            touch('delete')

        def flush(self):
            touch('flush')

        def rollback(self):
            touch('rollback')

        def expire_all(self):
            touch('expire_all')

        def remove(self):
            touch('remove')

    engine = SimpleNamespace(pool=None, dispose=Mock(), begin=Mock(), execute=Mock())
    run.db = SimpleNamespace(session=SessionDouble())
    run.boundary = SimpleNamespace(engine=engine, pool=None)
    schema, data = {'verified': 'schema'}, {'verified': 'data'}

    def snapshot(kind, result, actual_engine):
        assert actual_engine is engine
        touch(kind)
        return result

    monkeypatch.setattr(m, 'schema_snapshot', lambda engine: snapshot('schema', schema, engine))
    monkeypatch.setattr(m, 'data_snapshot', lambda engine: snapshot('data', data, engine))
    return run, control, operations, engine, schema, data


def test_real_crud_function_keeps_order_and_transaction_boundaries(crud_double):
    run, control, operations, engine, schema, data = crud_double
    run.verify_crud_and_snapshots()
    visited = list(dict.fromkeys(step for step, operation in operations))
    assert visited == list(DIAGNOSTIC_STEPS)
    assert sum(operation == 'commit' for _, operation in operations) == 8
    assert sum(operation == 'rollback' for _, operation in operations) == 4
    assert operations[:2] == [('insert_defaults_commit', 'add_all'), ('insert_defaults_commit', 'commit')]
    assert operations[-3:] == [('session_remove', 'remove'), ('schema_snapshot', 'schema'), ('data_snapshot', 'data')]
    assert run.verified_schema is schema and run.verified_data is data
    assert run.state == 'HEAD_VERIFIED' and not run.cleanup_authorized
    engine.begin.assert_not_called()
    engine.execute.assert_not_called()


@pytest.mark.parametrize('substage', DIAGNOSTIC_STEPS)
def test_real_crud_and_snapshot_failures_keep_last_marker_and_only_release(crud_double, substage):
    run, control, operations, engine, schema, data = crud_double
    primary = m.MigrationSafetyError('migration_orm_update_failed')
    control.fail_at, control.primary = substage, primary
    run.authorize_cleanup = Mock(side_effect=AssertionError('cleanup forbidden'))
    run.cleanup = Mock(side_effect=AssertionError('DROP forbidden'))
    # Exercise the same primary-exception/final-release contract as execute().
    primary_seen = None
    with pytest.raises(m.MigrationSafetyError) as result:
        try:
            run.verify_crud_and_snapshots()
        except BaseException as exc:
            primary_seen = exc
            raise
        finally:
            run.release(primary_seen)
    assert result.value is primary
    assert run.state == 'HEAD_VERIFIED' and not run.cleanup_authorized
    payload = m.safe_failure(primary, run)
    assert m.validate_result(payload)['substage'] == substage
    assert run.verified_schema is run.verified_data is None
    assert any(operation == 'remove' for _, operation in operations)
    engine.dispose.assert_called_once()
    engine.begin.assert_not_called()
    engine.execute.assert_not_called()
    run.authorize_cleanup.assert_not_called()
    run.cleanup.assert_not_called()


@pytest.mark.parametrize('actual_errno,substage,rollbacks', [(1452, 'record_unique', 1),
                                                          (1064, 'record_unique', 1),
                                                          (1062, 'card_foreign_key', 3)])
def test_real_constraint_conversion_retains_actual_not_expected_errno(crud_double, actual_errno, substage, rollbacks):
    run, control, operations, engine, _, _ = crud_double
    control.wrong_errno = actual_errno  # Never substitute the expected probe errno.
    with pytest.raises(m.MigrationSafetyError) as caught:
        run.verify_crud_and_snapshots()
    payload = m.safe_failure(caught.value, run)
    assert m.validate_result(payload)['error_code'] == 'migration_constraint_wrong_error'
    assert payload['substage'] == substage and payload['mysql_errno'] == actual_errno
    # Preserve the original transaction boundary: the failed require precedes rollback.
    assert sum(operation == 'rollback' for _, operation in operations) == rollbacks
    assert not run.cleanup_authorized and run.state == 'HEAD_VERIFIED'
    assert PASSWORD not in json.dumps(payload) and URL not in json.dumps(payload)


@pytest.mark.parametrize('invalid_errno', [True, False, '1062', 0, -1, 65536, 1062.5])
def test_converted_constraint_error_rejects_invalid_diagnostic_errno(crud_double, invalid_errno):
    run, control, _, _, _, _ = crud_double
    control.wrong_errno = invalid_errno
    with pytest.raises(m.MigrationSafetyError) as caught:
        run.verify_crud_and_snapshots()
    payload = m.safe_failure(caught.value, run)
    assert m.validate_result(payload)['error_code'] == 'migration_constraint_wrong_error'
    assert payload['mysql_errno'] is None


def test_constraint_not_enforced_does_not_fabricate_expected_errno(crud_double):
    run, control, operations, engine, _, _ = crud_double
    control.missing_constraint = True
    with pytest.raises(m.MigrationSafetyError) as caught:
        run.verify_crud_and_snapshots()
    result = m.safe_failure(caught.value, run)
    assert result['error_code'] == 'migration_constraint_not_enforced' and result['mysql_errno'] is None


def test_successful_constraint_errno_cannot_leak_into_later_failure(crud_double):
    run, control, _, _, _, _ = crud_double
    control.fail_at = 'card_delete'
    control.primary = m.MigrationSafetyError('migration_orm_delete_failed')
    with pytest.raises(m.MigrationSafetyError) as caught:
        run.verify_crud_and_snapshots()
    payload = m.safe_failure(caught.value, run)
    assert payload['substage'] == 'card_delete' and payload['mysql_errno'] is None


def test_release_failure_keeps_the_original_crud_exception_and_marker(crud_double):
    run, control, _, engine, _, _ = crud_double
    control.fail_at = 'verify_update'
    control.primary = m.MigrationSafetyError('migration_orm_update_failed')
    engine.dispose.side_effect = RuntimeError(PASSWORD + URL)
    run.db.session.remove = Mock(side_effect=RuntimeError('private release failure ' + PASSWORD))
    with pytest.raises(m.MigrationSafetyError) as caught:
        try:
            run.verify_crud_and_snapshots()
        finally:
            run.release(control.primary)
    assert caught.value is control.primary
    result = m.safe_failure(caught.value, run)
    assert result['error_code'] == 'migration_orm_update_failed' and result['substage'] == 'verify_update'
    assert result['mysql_errno'] is None and not run.cleanup_authorized
    run.db.session.remove.assert_called_once()
    engine.dispose.assert_called_once()
    assert PASSWORD not in json.dumps(result) and URL not in json.dumps(result)


def test_parent_accepts_new_crud_payload_without_forwarding_stderr(monkeypatch, capsys):
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    run = diagnostic_run()
    run.substage = 'card_unique'
    payload = m.safe_failure(driver_error(1062), run)
    monkeypatch.setattr(m.subprocess, 'run', Mock(return_value=SimpleNamespace(
        returncode=1, stdout=json.dumps(payload), stderr=URL + PASSWORD)))
    assert m.launch() == payload
    captured = capsys.readouterr()
    assert captured.out == captured.err == ''


def test_parent_rejects_unknown_substage_with_fixed_protocol_error(monkeypatch):
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    payload = m.safe_failure(driver_error(1062), diagnostic_run())
    payload['substage'] = URL + PASSWORD
    monkeypatch.setattr(m.subprocess, 'run', Mock(return_value=SimpleNamespace(
        returncode=1, stdout=json.dumps(payload), stderr=URL + PASSWORD)))
    with pytest.raises(m.MigrationSafetyError, match='^migration_child_protocol_invalid$'):
        m.launch()


def test_main_emits_only_safe_failure_json_and_nonzero_exit(monkeypatch, capsys):
    run = diagnostic_run()
    run.substage = 'insert_defaults_commit'
    run.execute = Mock(side_effect=driver_error(1062))
    monkeypatch.setattr(m, 'MigrationRun', lambda: run)
    monkeypatch.setattr(m.sys, 'argv', ['fixed-child-script'])
    assert m.main() == 1
    captured = capsys.readouterr()
    payload = m.validate_result(json.loads(captured.out))
    assert payload['stage'] == 'crud' and payload['substage'] == 'insert_defaults_commit'
    assert payload['mysql_errno'] == 1062 and payload['facts'] == {} and not captured.err
    assert PASSWORD not in captured.out and URL not in captured.out and 'fictional SQL' not in captured.out
