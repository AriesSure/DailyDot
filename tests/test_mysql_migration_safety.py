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
from tests import agent_admission_cases as admission_cases


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
            columns.append((c.name, kind, c.nullable, None, c.name == 'id' and table.name != 'conversations', False))
        result[table.name] = {'checks': m.verify_checks(table.name, [
            {'name': c.name, 'sqltext': str(c.sqltext)} for c in table.constraints
            if isinstance(c, sa.CheckConstraint)]),
            'foreign_actions': tuple(sorted((c.name, c.ondelete or 'RESTRICT')
                                            for c in table.foreign_key_constraints)),
            'columns': tuple(columns), 'pk': tuple(c.name for c in table.primary_key),
            'indexes': tuple(sorted(indexes)),
            'unique': tuple(sorted((name, cols) for name, unique, cols in indexes if unique)),
            'foreign': tuple(sorted((tuple(f.parent.name for f in c.elements), c.elements[0].column.table.name,
                                    tuple(f.column.name for f in c.elements)) for c in table.foreign_key_constraints)),
            'foreign_names': tuple(sorted(c.name or table.name + '_ibfk_' + str(i + 1)
                                          for i, c in enumerate(table.foreign_key_constraints))),
            'collation': 'utf8mb4_0900_ai_ci', 'charset': 'utf8mb4', 'engine': 'InnoDB'}
    result['alembic_version'] = {
        'columns': (('version_num', 'VARCHAR(32)', False, None, False, False),),
        'pk': ('version_num',), 'indexes': (), 'unique': (), 'checks': (), 'foreign_actions': (),
        'foreign': (), 'foreign_names': (),
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
    catalog.data['agent_admission_gate'] = (('global', 0),)

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
    run.admission_verified = True
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
                         'admission': dict.fromkeys(m.ADMISSION_FACTS, True),
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
            if run.substage == 'card_foreign_key':
                run.card_probe['rollback_observed'] = True

        def expire_all(self):
            touch('expire_all')

        def remove(self):
            touch('remove')

    engine = SimpleNamespace(pool=None, dispose=Mock(), begin=Mock(), execute=Mock())
    run.db = SimpleNamespace(session=SessionDouble())
    run.boundary = SimpleNamespace(engine=engine, pool=None)
    # Existing CRUD tests isolate SQL/diagnostic reads; dedicated tests below
    # exercise the real diagnostic methods with controlled Connection doubles.
    def start_probe():
        run.card_probe = dict.fromkeys(m.CARD_FACT_KEYS)
        run.card_probe.update(server_version='8.0.46', foreign_key_checks=1, parent_rows=0)
    run.start_card_probe = Mock(side_effect=start_probe)
    run.read_card_residue_after_rollback = Mock(side_effect=lambda: run.card_probe.update(residual_rows=0))
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


@pytest.fixture
def card_diagnostics(monkeypatch):
    """Offline Connection doubles; never evidence of actual MySQL results."""
    run = diagnostic_run()
    run.substage = 'card_foreign_key'
    run.app = Flask('card-diagnostic-double')
    run.server_identity = ('dailydot_test', 'dailydot_test@%', '8.0.46', ())
    control = SimpleNamespace(version='8.0.46', checks=1, parents=0, residual=0,
                              probe_error=False, residue_error=False, close_error=False, callback=None)
    statements, identities = [], []
    engine = SimpleNamespace(pool=None, dispose=Mock())

    class Connection:
        def __init__(self, kind):
            self.kind, self.engine = kind, engine
        def begin(self):
            assert self.kind == 'read'
        def execute(self, statement):
            sql = str(statement)
            statements.append((self, sql))
            assert sql.startswith('SELECT ')
            if self.kind == 'probe':
                if control.probe_error:
                    raise RuntimeError(PASSWORD + URL)
                assert '@@SESSION.foreign_key_checks' in sql and 'users WHERE id=999' in sql
                return SimpleNamespace(one=lambda: (control.version, control.checks, control.parents))
            if control.residue_error:
                raise RuntimeError(PASSWORD + URL)
            assert "cards WHERE user_id=999 AND date='2025-01-04'" in sql
            return SimpleNamespace(scalar_one=lambda: control.residual)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            if control.close_error:
                raise RuntimeError(PASSWORD)

    probe, read = Connection('probe'), Connection('read')
    engine.connect = Mock(return_value=read)
    run.boundary = SimpleNamespace(engine=engine, pool=None, check=Mock())
    run.db = SimpleNamespace(session=SimpleNamespace(connection=Mock(return_value=probe), remove=Mock()))
    def identity(actual_engine, *, connection):
        assert actual_engine is engine and connection in (probe, read)
        identities.append(connection)
        return run.server_identity
    run.verify_server_identity = Mock(side_effect=identity)
    def listen(connection, event, callback):
        assert connection is probe and event == 'rollback'
        control.callback = callback
    monkeypatch.setattr(m.sa.event, 'listen', listen)
    monkeypatch.setattr(m.sa.event, 'remove', Mock())
    return run, control, probe, read, statements, identities


def test_card_probe_reads_the_actual_session_connection(card_diagnostics):
    run, control, probe, read, statements, identities = card_diagnostics
    run.start_card_probe()
    assert identities == [probe] and statements[0][0] is probe
    assert run.card_probe == {**dict.fromkeys(m.CARD_FACT_KEYS), 'server_version': '8.0.46',
                              'foreign_key_checks': 1, 'parent_rows': 0}
    run.boundary.engine.connect.assert_not_called()


@pytest.mark.parametrize('field,value', [('version', '8.0.47'), ('checks', 0), ('parents', 1),
                                       ('version', URL), ('checks', True), ('parents', True)])
def test_card_preconditions_do_not_accept_wrong_or_untyped_values(card_diagnostics, field, value):
    run, control, *_ = card_diagnostics
    setattr(control, field, value)
    with pytest.raises(m.MigrationSafetyError, match='^migration_card_probe_unverified$'):
        run.start_card_probe()
    assert control.callback is None and not run.cleanup_authorized


def test_card_query_failure_is_unknown_not_zero(card_diagnostics):
    run, control, *_ = card_diagnostics
    control.probe_error = True
    with pytest.raises(m.MigrationSafetyError) as caught:
        run.start_card_probe()
    payload = m.safe_failure(caught.value, run)
    assert m.validate_result(payload)['facts']['card_foreign_key'] == dict.fromkeys(m.CARD_FACT_KEYS)
    assert PASSWORD not in json.dumps(payload) and URL not in json.dumps(payload)


def test_release_preserves_1216_and_reads_residue_on_separately_authorized_connection(card_diagnostics):
    run, control, probe, read, statements, identities = card_diagnostics
    run.start_card_probe()
    control.callback(probe)  # Simulate the pre-DBAPI rollback event, not an ACK.
    error = m.MigrationSafetyError('migration_constraint_wrong_error', mysql_errno=1216)
    run.release(error)
    assert identities == [probe, read] and statements[-1][0] is read and read is not probe
    assert run.card_probe['residual_rows'] == 0 and run.card_probe['session_removed'] is True
    assert run.card_probe['explicit_rollback_returned'] is None
    payload = m.safe_failure(error, run)
    assert m.validate_result(payload)['mysql_errno'] == 1216
    assert payload['error_code'] == 'migration_constraint_wrong_error'
    assert run.state == 'HEAD_VERIFIED' and not run.cleanup_authorized
    run.boundary.engine.dispose.assert_called_once()
    run.db.session.remove.assert_called_once()


@pytest.mark.parametrize('failure', ['query', 'identity', 'boundary', 'close', 'invalid_count', 'remove'])
def test_residue_failure_never_fabricates_zero_or_replaces_primary(card_diagnostics, failure):
    run, control, probe, *_ = card_diagnostics
    run.start_card_probe()
    control.callback(probe)
    if failure == 'query':
        control.residue_error = True
    elif failure == 'identity':
        run.verify_server_identity.side_effect = m.MigrationSafetyError('migration_server_identity_changed')
    elif failure == 'boundary':
        run.boundary.check.side_effect = m.MigrationSafetyError('migration_engine_changed')
    elif failure == 'close':
        control.close_error = True
    elif failure == 'invalid_count':
        control.residual = True
    else:
        run.db.session.remove.side_effect = RuntimeError(PASSWORD)
    primary = m.MigrationSafetyError('migration_constraint_wrong_error', mysql_errno=1216)
    with pytest.raises(m.MigrationSafetyError) as caught:
        try:
            raise primary
        finally:
            run.release(primary)
    assert caught.value is primary and run.card_probe['residual_rows'] is None
    assert not run.cleanup_authorized
    assert m.validate_result(m.safe_failure(primary, run))['mysql_errno'] == 1216


@pytest.mark.parametrize('observed,removed,explicit', [(False, True, False), (True, False, False)])
def test_residue_read_requires_rollback_observation_and_completed_session_operation(card_diagnostics, observed, removed, explicit):
    run, control, probe, *_ = card_diagnostics
    run.start_card_probe()
    run.card_probe.update(rollback_observed=observed, session_removed=removed,
                          explicit_rollback_returned=explicit)
    run.read_card_residue_after_rollback()
    run.boundary.engine.connect.assert_not_called()
    assert run.card_probe['residual_rows'] is None


def test_nonzero_residue_is_recorded_truthfully(card_diagnostics):
    run, control, probe, *_ = card_diagnostics
    run.start_card_probe()
    control.callback(probe)
    control.residual = 2
    run.card_probe['explicit_rollback_returned'] = True
    run.read_card_residue_after_rollback()
    assert run.card_probe['residual_rows'] == 2


@pytest.mark.parametrize('key,value', [('server_version', URL), ('foreign_key_checks', True),
                                    ('parent_rows', -1), ('residual_rows', '0'),
                                    ('session_removed', 1), ('password', PASSWORD)])
def test_card_facts_protocol_is_closed_and_typed(key, value):
    run = diagnostic_run()
    run.substage = 'card_foreign_key'
    run.card_probe = dict.fromkeys(m.CARD_FACT_KEYS)
    payload = m.safe_failure(m.MigrationSafetyError('migration_constraint_wrong_error'), run)
    payload['facts']['card_foreign_key'][key] = value
    with pytest.raises(m.MigrationSafetyError, match='^migration_child_protocol_invalid$'):
        m.validate_result(payload)


@pytest.mark.parametrize('errno', [1216, 1452])
def test_card_foreign_errno_compatibility_keeps_rollback_residue_and_later_crud(crud_double, errno):
    run, control, *_ = crud_double
    # Override only the card FK probe; earlier unique probes still return 1062.
    original = run.db.session.commit
    def commit():
        if run.substage == 'card_foreign_key':
            raise driver_error(errno)
        return original()
    run.db.session.commit = commit
    run.verify_crud_and_snapshots()
    assert run.card_probe['explicit_rollback_returned'] is True
    assert run.card_probe['rollback_observed'] is True and run.card_probe['residual_rows'] == 0
    run.read_card_residue_after_rollback.assert_called_once()
    steps = {step for step, operation in crud_double[2]}
    assert {'todo_duplicate', 'card_delete', 'card_survivor', 'schema_snapshot', 'data_snapshot'} <= steps
    assert not run.cleanup_authorized and run.state == 'HEAD_VERIFIED'


def test_success_release_does_not_query_already_cleaned_tables(card_diagnostics):
    run, control, probe, *_ = card_diagnostics
    run.start_card_probe()
    control.callback(probe)
    run.state, run.stage, run.substage = 'EMPTY_VERIFIED', 'release', None
    run.release()
    run.boundary.engine.connect.assert_not_called()
    assert run.state == 'DISPOSED' and not run.cleanup_authorized


def test_probe_identity_failure_keeps_original_fixed_error(card_diagnostics):
    run, *_ = card_diagnostics
    primary = m.MigrationSafetyError('migration_server_identity_changed')
    run.verify_server_identity.side_effect = primary
    with pytest.raises(m.MigrationSafetyError) as caught:
        run.start_card_probe()
    assert caught.value is primary
    assert all(value is None for value in run.card_probe.values())


@pytest.mark.parametrize('errno', [1062, 1451, 1215, 1048, None, True, '1216'])
def test_card_fk_rejects_other_or_unverified_errnos(crud_double, errno):
    run, _, operations, engine, *_ = crud_double
    original = run.db.session.commit
    def commit():
        if run.substage == 'card_foreign_key':
            raise driver_error(errno)
        return original()
    run.db.session.commit = commit
    with pytest.raises(m.MigrationSafetyError) as caught:
        run.verify_crud_and_snapshots()
    payload = m.validate_result(m.safe_failure(caught.value, run))
    assert payload['error_code'] == 'migration_constraint_wrong_error'
    assert payload['mysql_errno'] == (errno if type(errno) is int else None)
    assert run.state == 'HEAD_VERIFIED' and not run.cleanup_authorized
    assert not any(step == 'todo_duplicate' for step, operation in operations)
    engine.begin.assert_not_called()
    assert PASSWORD not in json.dumps(payload) and URL not in json.dumps(payload)


@pytest.mark.parametrize('substage', ['record_unique', 'card_unique'])
@pytest.mark.parametrize('errno', [1216, 1452])
def test_foreign_errnos_cannot_satisfy_other_constraint_probes(crud_double, substage, errno):
    run, *_ = crud_double
    original = run.db.session.commit
    def commit():
        if run.substage == substage:
            raise driver_error(errno)
        return original()
    run.db.session.commit = commit
    with pytest.raises(m.MigrationSafetyError) as caught:
        run.verify_crud_and_snapshots()
    payload = m.safe_failure(caught.value, run)
    assert payload['substage'] == substage and payload['mysql_errno'] == errno
    assert payload['error_code'] == 'migration_constraint_wrong_error' and not run.cleanup_authorized


def test_missing_parent_without_integrity_error_still_fails(crud_double):
    run, *_ = crud_double
    original = run.db.session.commit
    run.db.session.commit = lambda: None if run.substage == 'card_foreign_key' else original()
    with pytest.raises(m.MigrationSafetyError, match='^migration_constraint_not_enforced$'):
        run.verify_crud_and_snapshots()
    run.read_card_residue_after_rollback.assert_not_called()
    assert run.state == 'HEAD_VERIFIED' and not run.cleanup_authorized


@pytest.mark.parametrize('field,value', [('residual_rows', 1), ('residual_rows', None),
                                       ('residual_rows', False), ('foreign_key_checks', 0),
                                       ('parent_rows', 1), ('rollback_observed', None)])
@pytest.mark.parametrize('errno', [1216, 1452])
def test_accepted_errno_requires_verified_clean_rollback_facts(crud_double, field, value, errno):
    run, _, operations, engine, *_ = crud_double
    original = run.db.session.commit
    def commit():
        if run.substage == 'card_foreign_key':
            raise driver_error(errno)
        return original()
    run.db.session.commit = commit
    def read_residue():
        run.card_probe.update(residual_rows=0)
        run.card_probe[field] = value
    run.read_card_residue_after_rollback.side_effect = read_residue
    with pytest.raises(m.MigrationSafetyError) as caught:
        run.verify_crud_and_snapshots()
    payload = m.safe_failure(caught.value, run)
    assert payload['error_code'] == 'migration_card_probe_unverified' and payload['mysql_errno'] == errno
    assert not run.cleanup_authorized and run.state == 'HEAD_VERIFIED'
    assert not any(step == 'todo_duplicate' for step, operation in operations)
    engine.begin.assert_not_called()


def test_fk_requires_native_dbapi_integrity_error(crud_double):
    run, *_ = crud_double
    original = run.db.session.commit
    def commit():
        if run.substage == 'card_foreign_key':
            raise sa.exc.IntegrityError(URL, None, pymysql.OperationalError(1216, PASSWORD))
        return original()
    run.db.session.commit = commit
    with pytest.raises(m.MigrationSafetyError, match='^migration_constraint_wrong_error$'):
        run.verify_crud_and_snapshots()
    assert not run.cleanup_authorized


@pytest.mark.parametrize('name,rules', [
    ('chat_turns', [{'name': 'unknown_check', 'sqltext': 'turn_no >= 1'}]),
    ('chat_turns', [{'name': 'ck_chat_turns_number', 'sqltext': 'turn_no >= 0'}]),
    ('users', [{'name': 'ck_users_fake', 'sqltext': 'id >= 1'}]),
])
def test_chat_check_whitelist_rejects_unknown_rules(name, rules):
    with pytest.raises(m.MigrationSafetyError, match='migration_unknown_check'):
        m.verify_checks(name, rules)


def test_chat_check_normalization_preserves_literal_semantics():
    assert m.normalize_check("role IN ('us er','assistant','tool')") != m.normalize_check(
        "role IN ('user','assistant','tool')")
    assert m.normalize_check("status IN ('PENDING')") != m.normalize_check("status IN ('pending')")
    rules = [{'name': key, 'sqltext': value} for key, value in m.CHECK_RULES['chat_turns'].items()]
    assert m.verify_checks('chat_turns', rules)
    reflected = [{'name': row['name'], 'sqltext': row['sqltext'].replace('IN', 'in').replace('AND', 'and')}
                 for row in rules]
    assert m.verify_checks('chat_turns', reflected) == m.verify_checks('chat_turns', rules)


@pytest.mark.parametrize('table,alter', [
    ('conversations', 'autoincrement'), ('chat_turns', 'autoincrement'),
    ('chat_turns', 'cascade'), ('chat_messages', 'cascade'), ('chat_messages', 'check'),
])
def test_chat_schema_tampering_cannot_authorize_cleanup(catalog, table, alter):
    run, data = catalog
    if alter == 'autoincrement':
        columns = list(data.schema[table]['columns'])
        row = list(columns[0])
        row[4] = not row[4]
        columns[0] = tuple(row)
        data.schema[table]['columns'] = tuple(columns)
    elif alter == 'cascade':
        data.schema[table]['foreign_actions'] = ((data.schema[table]['foreign_names'][0], 'RESTRICT'),)
    else:
        data.schema[table]['checks'] = ()
    with pytest.raises(m.MigrationSafetyError):
        run.authorize_cleanup()
    assert not run.cleanup_authorized and data.drops == []


def test_chat_cleanup_dependency_order(catalog):
    _, data = catalog
    order = m.cleanup_order(data.schema)
    assert order.index('chat_messages') < order.index('chat_turns') < order.index('conversations')
    assert order.index('conversations') < order.index('users')
    assert order.index('users') < order.index('agent_admission_gate') < order.index('alembic_version')


def test_missing_admission_proof_never_authorizes_cleanup(catalog):
    run, data = catalog
    run.admission_verified = False
    with pytest.raises(m.MigrationSafetyError, match='migration_admission_not_verified'):
        run.authorize_cleanup()
    assert not run.cleanup_authorized and data.drops == []


@pytest.mark.parametrize('change', ['pk', 'auto', 'check'])
def test_gate_schema_is_exact_not_a_generic_string_pk_exception(catalog, change):
    from app.extensions import db
    _, data = catalog
    schema = deepcopy(data.schema)
    gate = schema['agent_admission_gate']
    if change == 'pk':
        gate['pk'] = ('lock_version',)
    elif change == 'auto':
        columns = list(gate['columns'])
        row = list(columns[0])
        row[4] = True
        columns[0] = tuple(row)
        gate['columns'] = tuple(columns)
    else:
        gate['checks'] = ()
    with pytest.raises(m.MigrationSafetyError):
        m.verify_head(schema, db.metadata, data.schema['users']['collation'])


def test_chat_check_cannot_regroup_boolean_and_equality():
    rows = [{'name': key, 'sqltext': value} for key, value in m.CHECK_RULES['chat_turns'].items()]
    rows[0]['sqltext'] = ("(status IN ('pending','completed','failed','interrupted') AND length(status)) "
                          "= length(trim(status))")
    with pytest.raises(m.MigrationSafetyError, match='migration_unknown_check'):
        m.verify_checks('chat_turns', rows)


def test_direct_cli_defines_chat_crud_before_main(monkeypatch, capsys):
    import runpy
    import sys
    path = str(m.ROOT / 'tests/mysql_migration_ci.py')
    previous = sys.gettrace()
    reached = []

    def trace(frame, event, arg):
        if event == 'call' and frame.f_code.co_filename == path and frame.f_code.co_name == 'main':
            namespace = frame.f_globals
            assert callable(namespace.get('verify_chat_crud'))
            reached.append(True)
            namespace['MigrationRun'].execute = lambda self: {'status': 'passed'}
        return trace

    monkeypatch.setattr(sys, 'argv', [path])
    try:
        sys.settrace(trace)
        with pytest.raises(SystemExit) as exit_info:
            runpy.run_path(path, run_name='__main__')
    finally:
        sys.settrace(previous)
    assert exit_info.value.code == 0 and reached == [True]
    assert json.loads(capsys.readouterr().out)['status'] == 'passed'


@pytest.mark.parametrize('table,name', [
    ('conversations', 'fk_conversations_user'),
    ('chat_turns', 'fk_chat_turns_conversation'),
    ('chat_messages', 'fk_chat_messages_turn'),
])
def test_named_chat_foreign_key_expectation_is_exact(catalog, table, name):
    run, data = catalog
    assert data.schema[table]['foreign_names'] == (name,)
    data.schema[table]['foreign_names'] = ('unexpected_fk',)
    data.schema[table]['foreign_actions'] = (('unexpected_fk', 'CASCADE' if table != 'conversations' else 'RESTRICT'),)
    with pytest.raises(m.MigrationSafetyError):
        run.authorize_cleanup()
    assert not run.cleanup_authorized and data.drops == []


@pytest.mark.parametrize('exception_class,driver_class', [
    (sa.exc.OperationalError, pymysql.OperationalError), (sa.exc.IntegrityError, pymysql.IntegrityError)])
@pytest.mark.parametrize('table,constraint,substage', [
    ('chat_messages', 'ck_chat_messages_role', 'chat_role_check'),
    ('chat_turns', 'ck_chat_turns_status', 'chat_status_check'),
])
def test_chat_check_3819_is_expected_database_rejection(exception_class, driver_class, table, constraint, substage):
    run = m.MigrationRun()
    run.stage, run.substage = 'crud', substage
    db = SimpleNamespace(session=Mock())
    db.session.commit.side_effect = exception_class(
        'private SQL', {'password': PASSWORD},
        driver_class(3819, f"Check constraint '{constraint}' is violated."))
    row = SimpleNamespace(__tablename__=table)
    m.chat_constraint_probe(db, run, row, (3819,), constraint)
    db.session.rollback.assert_called_once_with()
    assert run.chat_diagnostic is None and not run.cleanup_authorized


@pytest.mark.parametrize('errno', [1062, 1452, None, True])
def test_chat_check_does_not_accept_other_database_errors(errno):
    run = m.MigrationRun()
    run.stage, run.substage = 'crud', 'chat_role_check'
    db = SimpleNamespace(session=Mock())
    db.session.commit.side_effect = sa.exc.OperationalError(
        'private SQL', None, pymysql.OperationalError(errno, "Check constraint 'ck_chat_messages_role' is violated."))
    with pytest.raises(m.MigrationSafetyError):
        m.chat_constraint_probe(db, run, SimpleNamespace(__tablename__='chat_messages'), (3819,),
                                'ck_chat_messages_role')
    assert not run.cleanup_authorized
    payload = m.validate_result(m.safe_failure(m.MigrationSafetyError('migration_constraint_wrong_error'), run))
    assert payload['substage'] == 'chat_role_check'
    assert payload['facts']['chat_constraint']['constraint'] == 'ck_chat_messages_role'
    assert PASSWORD not in json.dumps(payload)


def test_chat_constraint_not_enforced_is_still_failure():
    run = m.MigrationRun()
    db = SimpleNamespace(session=Mock())
    with pytest.raises(m.MigrationSafetyError, match='migration_constraint_not_enforced'):
        m.chat_constraint_probe(db, run, SimpleNamespace(__tablename__='chat_messages'), (3819,),
                                'ck_chat_messages_role')
    assert not run.cleanup_authorized


@pytest.mark.parametrize('message,name', [
    ("private " + PASSWORD, 'unknown'),
    ("Check constraint 'ck_chat_turns_status' is violated.", 'ck_chat_turns_status'),
])
def test_wrong_constraint_or_unknown_message_diagnostics_are_private(message, name):
    run = m.MigrationRun()
    run.stage, run.substage = 'crud', 'chat_role_check'
    db = SimpleNamespace(session=Mock())
    db.session.commit.side_effect = sa.exc.OperationalError(
        'private SQL', None, pymysql.OperationalError(1062, message))
    with pytest.raises(m.MigrationSafetyError) as failure:
        m.chat_constraint_probe(db, run, SimpleNamespace(__tablename__='chat_messages'), (3819,),
                                'ck_chat_messages_role')
    payload = m.validate_result(m.safe_failure(failure.value, run))
    assert payload['facts']['chat_constraint']['constraint'] == name
    assert payload['facts']['chat_constraint']['sqlstate'] == 'unknown'
    assert PASSWORD not in json.dumps(payload) and 'private SQL' not in json.dumps(payload)


@pytest.mark.parametrize('substage', m.SCHEMA_SUBSTAGES)
def test_schema_substage_is_preserved_without_raw_exception(substage):
    run = m.MigrationRun()
    run.stage, run.substage = 'schema', substage
    result = m.validate_result(m.safe_failure(RuntimeError(PASSWORD), run))
    assert result['substage'] == substage and PASSWORD not in json.dumps(result)


# Offline diagnostic protocol tests; no real MySQL behavior is claimed here.

def admission_diagnostic(assertion_id='same_user_one_winner'):
    return {'scenario_id': admission_cases.ASSERTIONS[assertion_id], 'assertion_id': assertion_id,
            'expected': list(admission_cases.EXPECTED[assertion_id]), 'actual': ['agent_admission_unavailable'],
            'outcome_codes': ['agent_admission_unavailable'],
            'time_precision': admission_cases.probe_failure('unknown')}


def admission_failure(diagnostic):
    run = SimpleNamespace(stage='crud', substage='admission_concurrency', state='HEAD_VERIFIED')
    return m.safe_failure(admission_cases.AdmissionDiagnosticError(diagnostic), run)


@pytest.mark.parametrize('assertion_id', tuple(admission_cases.ASSERTIONS))
def test_admission_assertion_protocol_roundtrip(assertion_id):
    value = admission_diagnostic(assertion_id)
    payload = admission_failure(value)
    assert payload['error_code'] == 'migration_admission_assertion_failed'
    assert m.validate_result(json.loads(json.dumps(payload)))['facts'] == {'admission': value}


@pytest.mark.parametrize('field,value', [
    ('assertion_id', 'unknown'), ('scenario_id', 'unknown'), ('actual', ['unknown']),
    ('outcome_codes', ['unknown']), ('expected', ['unknown']), ('actual', [True]),
    ('time_precision', {}), ('actual', []), ('actual', ['created'] * 3),
])
def test_invalid_admission_diagnostic_rejected(field, value):
    diagnostic = admission_diagnostic()
    diagnostic[field] = value
    assert not admission_cases.valid_admission_diagnostic(diagnostic)
    payload = admission_failure(diagnostic)
    assert payload['error_code'] == 'mysql_forward_migration_failed' and payload['facts'] == {}
    payload['error_code'] = 'migration_admission_assertion_failed'
    payload['facts'] = {'admission': diagnostic}
    with pytest.raises(m.MigrationSafetyError, match='migration_child_protocol_invalid'):
        m.validate_result(payload)


def test_admission_secret_and_missing_facts_never_exported():
    diagnostic = admission_diagnostic()
    diagnostic['secret'] = URL + PASSWORD + ' private SQL parameters'
    payload = admission_failure(diagnostic)
    assert PASSWORD not in json.dumps(payload) and 'private SQL' not in json.dumps(payload)
    payload['error_code'] = 'migration_admission_assertion_failed'
    with pytest.raises(m.MigrationSafetyError, match='migration_child_protocol_invalid'):
        m.validate_result(payload)
    assert admission_cases.outcome(URL) == 'other'
    assert admission_cases.outcome(1) == 'other'
    assert admission_cases.outcome(True) == 'created'
    assert admission_cases.outcome(False) == 'duplicate'


def time_diagnostic():
    return {'verified': True, 'server_version': '8.0.46', 'truncate_fractional': False,
            'datetime_precision': 0, 'utc_microsecond': 750000, 'future_rows': 1, 'max_future_us': 250000,
            'samples': [{'input_us': v, 'read_us': 0, 'delta_us': -v, 'greater_than_input': False}
                        for v in (250000, 500000, 750000)]}


def test_time_precision_diagnostic_protocol_roundtrip():
    diagnostic = admission_diagnostic()
    diagnostic['time_precision'] = time_diagnostic()
    assert m.validate_result(admission_failure(diagnostic))['facts']['admission'] == diagnostic


@pytest.mark.parametrize('field,value', [
    ('server_version', URL), ('datetime_precision', 7), ('datetime_precision', True),
    ('utc_microsecond', 1000000), ('truncate_fractional', 1), ('future_rows', -1),
    ('max_future_us', 86400000001), ('samples', []), ('verified', 1),
])
def test_time_precision_types_and_bounds(field, value):
    diagnostic = time_diagnostic()
    diagnostic[field] = value
    assert not admission_cases.valid_time_diagnostic(diagnostic)


def test_admission_failure_never_authorizes_cleanup(catalog):
    run, data = catalog
    run.admission_verified = False
    payload = admission_failure(admission_diagnostic())
    assert m.validate_result(payload)['status'] == 'failed'
    with pytest.raises(m.MigrationSafetyError, match='migration_admission_not_verified'):
        run.authorize_cleanup()
    assert not run.cleanup_authorized and not data.drops


def test_time_probe_query_failure_rolls_back_without_fabrication():
    connection = Mock()
    connection.__enter__ = Mock(return_value=connection)
    connection.__exit__ = Mock(return_value=False)
    engine = Mock(dialect=SimpleNamespace(name='mysql'))
    configure_probe_connections(engine, connection)
    session = Mock()
    session.__enter__ = Mock(return_value=session)
    session.__exit__ = Mock(return_value=False)
    session.execute.side_effect = RuntimeError(URL + PASSWORD)
    with patch.object(admission_cases, 'Session', return_value=session):
        assert admission_cases.mysql_time_probe(engine, None) == admission_cases.probe_failure(
            'server_metadata', RuntimeError())
    session.rollback.assert_called_once()


def test_future_time_query_failure_is_unknown_not_zero():
    engine = Mock()
    engine.connect.side_effect = RuntimeError(PASSWORD)
    diagnostic = time_diagnostic()
    result = admission_cases.future_time_probe(engine, 1, diagnostic, None)
    assert result['future_rows'] is result['max_future_us'] is None
    assert diagnostic['future_rows'] == 1  # No mutation of original evidence.


def test_rollback_precision_probe_offline_sql_roundtrips():
    # Stub SQL responses exercise collection only, never a claim about MySQL rounding.
    from datetime import datetime
    connection = Mock()
    connection.__enter__ = Mock(return_value=connection)
    connection.__exit__ = Mock(return_value=False)
    connection.execute.return_value.scalar_one.return_value = 0
    engine = Mock(dialect=SimpleNamespace(name='mysql'))
    configure_probe_connections(engine, connection)
    session = Mock()
    session.__enter__ = Mock(return_value=session)
    session.__exit__ = Mock(return_value=False)
    responses = [Mock(one=Mock(return_value=('8.0.46', '', datetime(2026, 1, 1, microsecond=750000)))),
                 Mock(scalar_one=Mock(return_value=0))]
    responses += [Mock(one=Mock(return_value=(datetime(2026, 1, 1, 12, 0, second), greater)))
                  for second, greater in ((0, 0), (1, 1), (1, 1))]
    session.execute.side_effect = responses
    with patch.object(admission_cases, 'Session', return_value=session):
        result = admission_cases.mysql_time_probe(engine, None)
    assert result['verified'] is True
    assert [r['input_us'] for r in result['samples']] == [250000, 500000, 750000]
    assert [r['delta_us'] for r in result['samples']] == [-250000, 500000, 250000]
    assert len(session.execute.call_args_list) == 5 and session.flush.call_count == 5
    session.rollback.assert_called_once()
    assert connection.execute.call_count == 2  # Independently observe row rollback.


@pytest.mark.parametrize('field,value', [('time_precision', None), ('expected', ['other'])])
def test_admission_missing_time_or_forged_expectation_rejected(field, value):
    diagnostic = admission_diagnostic()
    diagnostic[field] = value
    assert not admission_cases.valid_admission_diagnostic(diagnostic)


@pytest.mark.parametrize('field,value', [('read_us', True), ('delta_us', 1000001),
                                         ('greater_than_input', 1), ('input_us', 1)])
def test_time_probe_sample_fields_strict(field, value):
    diagnostic = time_diagnostic()
    diagnostic['samples'][0][field] = value
    assert not admission_cases.valid_time_diagnostic(diagnostic)


def run_probe_stub(failed_phase=None, rollback_fails=False, error=None):
    """Offline SQL/session response stub, no MySQL connection or rounding claim."""
    from datetime import datetime
    error = error or RuntimeError(PASSWORD)
    connection = Mock()
    connection.__enter__ = Mock(return_value=connection)
    connection.__exit__ = Mock(return_value=False)
    session = Mock()
    session.__enter__ = Mock(return_value=session)
    session.__exit__ = Mock(return_value=False)
    engine = Mock(dialect=SimpleNamespace(name='mysql'))
    residue_connection = configure_probe_connections(engine, connection)
    sql_phases = iter(('server_metadata', 'column_metadata', 'turn_readback_250',
                       'turn_readback_500', 'turn_readback_750'))
    flush_phases = iter(('synthetic_user_insert', 'conversation_insert', 'turn_insert_250',
                         'turn_insert_500', 'turn_insert_750'))
    check_phases = iter(('rollback_user_check', 'rollback_conversation_check'))

    def execute(statement, *args):
        phase = next(sql_phases)
        if phase == failed_phase:
            raise error
        if phase == 'server_metadata':
            return Mock(one=Mock(return_value=('8.0.46', '', datetime(2026, 1, 1))))
        if phase == 'column_metadata':
            return Mock(scalar_one=Mock(return_value=0))
        return Mock(one=Mock(return_value=(datetime(2026, 1, 1, 12), 0)))

    def flush():
        if next(flush_phases) == failed_phase:
            raise error

    def residue(statement):
        if next(check_phases) == failed_phase:
            raise error
        return Mock(scalar_one=Mock(return_value=0))

    def verify(checked):
        assert checked in (connection, residue_connection) and checked.in_transaction() is True
        if failed_phase == 'connection_identity':
            raise error

    session.execute.side_effect = execute
    session.flush.side_effect = flush
    connection.execute.side_effect = residue
    if rollback_fails or failed_phase == 'transaction_rollback':
        session.rollback.side_effect = ValueError(PASSWORD)
    real_validate = admission_cases.valid_time_diagnostic

    def validate(value):
        if failed_phase == 'result_validation':
            raise error
        return real_validate(value)

    with patch.object(admission_cases, 'Session', return_value=session) as factory, patch.object(
            admission_cases, 'valid_time_diagnostic', side_effect=validate):
        result = admission_cases.mysql_time_probe(engine, verify)
    if factory.called:
        factory.assert_called_once_with(connection)
    return result, session, connection


@pytest.mark.parametrize('phase', [p for p in admission_cases.PROBE_PHASES if p != 'unknown'])
def test_probe_phase_exception_reports_first_failure(phase):
    result, session, connection = run_probe_stub(phase)
    assert not result['verified'] and result['probe_phase'] == phase
    assert admission_cases.valid_time_diagnostic(result)
    assert PASSWORD not in json.dumps(result)
    assert result['exception_type'] == ('ValueError' if phase == 'transaction_rollback' else 'RuntimeError')
    if phase != 'connection_identity':
        session.rollback.assert_called_once()
    assert not session.commit.called and not connection.commit.called
    diagnostic = admission_diagnostic('time_probe_verified')
    diagnostic['time_precision'] = result
    assert m.validate_result(admission_failure(diagnostic))['facts']['admission'] == diagnostic


def test_primary_and_rollback_failures_are_both_preserved():
    result, _, _ = run_probe_stub('turn_insert_500', rollback_fails=True)
    assert result['probe_phase'] == 'turn_insert_500' and result['exception_type'] == 'RuntimeError'
    assert result['rollback_failure']['probe_phase'] == 'transaction_rollback'
    assert result['rollback_failure']['exception_type'] == 'ValueError'
    assert admission_cases.valid_time_diagnostic(result)


@pytest.mark.parametrize('errno,state,expected_errno,expected_state', [
    (1216, '23000', 1216, '23000'), (True, '23000', None, '23000'),
    (0, 'HY000', None, 'HY000'), (65536, '23000', None, '23000'),
    (1216, 'secret', 1216, None), (1216, '23!00', 1216, None),
])
def test_probe_dbapi_metadata_is_bounded(errno, state, expected_errno, expected_state):
    error = pymysql.OperationalError(errno, PASSWORD)
    error.sqlstate = state
    result, _, _ = run_probe_stub('column_metadata', error=sa.exc.OperationalError('private SQL', {}, error))
    assert result['failure_kind'] == 'dbapi'
    assert result['mysql_errno'] == expected_errno and result['sqlstate'] == expected_state
    assert PASSWORD not in json.dumps(result) and 'private SQL' not in json.dumps(result)


def test_probe_unknown_exception_and_phase_are_fixed():
    class SecretException(Exception):
        pass
    result = admission_cases.probe_failure(PASSWORD, SecretException(PASSWORD))
    assert result['probe_phase'] == 'unknown'
    assert result['exception_type'] == result['failure_kind'] == 'other'
    assert PASSWORD not in json.dumps(result)


@pytest.mark.parametrize('field,value', [
    ('probe_phase', 'invalid'), ('failure_kind', 'invalid'), ('exception_type', 'SecretException'),
    ('mysql_errno', True), ('mysql_errno', 65536), ('sqlstate', 'invalid'),
    ('sqlstate', '23!00'), ('rollback_failure', {}),
])
def test_invalid_probe_error_protocol_rejected(field, value):
    result = admission_cases.probe_failure('server_metadata', RuntimeError())
    result[field] = value
    assert not admission_cases.valid_time_diagnostic(result)
    diagnostic = admission_diagnostic()
    diagnostic['time_precision'] = result
    payload = admission_failure(diagnostic)
    assert payload['facts'] == {} and payload['error_code'] == 'mysql_forward_migration_failed'
    payload['facts'] = {'admission': diagnostic}
    with pytest.raises(m.MigrationSafetyError, match='migration_child_protocol_invalid'):
        m.validate_result(payload)


@pytest.mark.parametrize('change', ['missing', 'extra', 'rollback_extra'])
def test_probe_error_field_sets_are_exact(change):
    result = admission_cases.probe_failure('server_metadata')
    if change == 'missing':
        del result['exception_type']
    elif change == 'extra':
        result['message'] = PASSWORD
    else:
        result['rollback_failure'] = {**admission_cases.probe_error('transaction_rollback'), 'message': PASSWORD}
    assert not admission_cases.valid_time_diagnostic(result)


def test_failed_precision_probe_cannot_cleanup(catalog):
    run, data = catalog
    result, _, _ = run_probe_stub('turn_readback_750')
    diagnostic = admission_diagnostic('time_probe_verified')
    diagnostic['time_precision'] = result
    assert m.validate_result(admission_failure(diagnostic))['status'] == 'failed'
    run.admission_verified = False
    with pytest.raises(m.MigrationSafetyError, match='migration_admission_not_verified'):
        run.authorize_cleanup()
    assert not run.cleanup_authorized and not data.drops


def configure_probe_connections(engine, connection):
    """Offline Connection lifecycle stub, not evidence of live MySQL behavior."""
    residue = Mock()
    for conn in (connection, residue):
        conn.__enter__ = Mock(return_value=conn)
        conn.__exit__ = Mock(return_value=False)
        conn.engine = engine
        conn.closed = conn.invalidated = False
        conn.connection.driver_connection = object()
        state = {'active': False}
        conn.in_transaction.side_effect = lambda state=state: state['active']
        conn.begin.side_effect = lambda state=state: state.update(active=True)
        conn.rollback.side_effect = lambda state=state: state.update(active=False)
    residue.execute = connection.execute
    engine.connect.side_effect = [connection, residue]
    return residue


def test_probe_identity_transaction_and_session_connection_order():
    # Intercept the existing offline stub to observe actual helper call ordering.
    original = configure_probe_connections
    seen = []

    def configure(engine, connection):
        residue = original(engine, connection)
        real_rollback = connection.rollback.side_effect
        connection.rollback.side_effect = lambda: (seen.append('identity_rollback'), real_rollback())[-1]
        return residue

    with patch(__name__ + '.configure_probe_connections', side_effect=configure):
        with patch.object(
                admission_cases, 'check_probe_connection', wraps=admission_cases.check_probe_connection) as check:
            result, session, connection = run_probe_stub()
    assert result['verified'] is True
    assert check.call_args_list[0].args == (connection, connection.engine, True)
    assert check.call_args_list[1].args == (connection, connection.engine, False)
    assert check.call_args_list[2].args == (connection, connection.engine, False)
    assert seen == ['identity_rollback']
    assert session.flush.call_count == 5


@pytest.mark.parametrize('failure', ['identity', 'rollback', 'invalidated', 'still_active', 'physical_changed'])
def test_identity_failure_never_creates_probe_session(failure):
    connection = Mock()
    engine = Mock(dialect=SimpleNamespace(name='mysql'))
    configure_probe_connections(engine, connection)

    def identity(conn):
        assert conn is connection and conn.in_transaction() is True
        if failure == 'identity':
            raise m.MigrationSafetyError('migration_server_identity_changed')
        if failure == 'invalidated':
            conn.invalidated = True
        if failure == 'physical_changed':
            conn.connection.driver_connection = object()
    if failure == 'rollback':
        connection.rollback.side_effect = RuntimeError(PASSWORD)
    elif failure == 'still_active':
        connection.rollback.side_effect = None
    with patch.object(admission_cases, 'Session') as session:
        result = admission_cases.mysql_time_probe(engine, identity)
    assert result['verified'] is False and result['probe_phase'] == 'connection_identity'
    session.assert_not_called()


@pytest.mark.parametrize('failure', [None, 'identity', 'rollback', 'invalidated', 'still_active'])
def test_future_probe_transaction_contract(failure):
    connection = Mock()
    engine = Mock()
    configure_probe_connections(engine, connection)
    connection.execute.return_value.one.return_value = (1, 250000)

    def verify(conn):
        assert conn is connection and conn.in_transaction() is True
        if failure == 'identity':
            raise m.MigrationSafetyError('migration_server_identity_changed')
        if failure == 'invalidated':
            conn.invalidated = True
    if failure == 'rollback':
        connection.rollback.side_effect = RuntimeError(PASSWORD)
    elif failure == 'still_active':
        connection.rollback.side_effect = None
    result = admission_cases.future_time_probe(engine, 1, time_diagnostic(), verify)
    assert (result['future_rows'], result['max_future_us']) == ((1, 250000) if failure is None else (None, None))
    connection.rollback.assert_called_once()
    if failure == 'identity':
        connection.execute.assert_not_called()
    assert not connection.commit.called


def test_residue_connection_must_be_independent_and_identity_checked():
    original = configure_probe_connections
    connections = []

    def configure(engine, connection):
        residue = original(engine, connection)
        connections.extend((connection, residue))
        return residue

    with patch(__name__ + '.configure_probe_connections', side_effect=configure):
        result, _, _ = run_probe_stub()
    assert result['verified'] is True
    assert connections[0] is not connections[1]
    assert connections[0].connection.driver_connection is not connections[1].connection.driver_connection
    for connection in connections:
        assert connection.in_transaction() is False
        connection.begin.assert_called_once()
        connection.rollback.assert_called_once()
        assert not connection.commit.called


@pytest.mark.parametrize('change', ['same_physical', 'residue_identity', 'residue_rollback'])
def test_residue_verification_failure_never_passes(change):
    original = configure_probe_connections

    def configure(engine, connection):
        residue = original(engine, connection)
        if change == 'same_physical':
            residue.connection.driver_connection = connection.connection.driver_connection
        elif change == 'residue_identity':
            residue.invalidated = True
        else:
            residue.rollback.side_effect = RuntimeError(PASSWORD)
        return residue

    with patch(__name__ + '.configure_probe_connections', side_effect=configure):
        result, _, _ = run_probe_stub()
    assert not result['verified']
    assert PASSWORD not in json.dumps(result)


# Deterministic Future collection tests: no database or scheduler sleeps.
def future_case(worker_id, stage, *, value=None, error=None, cancelled=False, pending=False):
    from concurrent.futures import Future, TimeoutError as FutureTimeoutError

    class ControlledFuture(Future):
        def result(self, timeout=None):
            if not self.done():
                assert timeout == 40
                raise FutureTimeoutError()
            return super().result(timeout=0)
    future = ControlledFuture()
    trace = admission_cases.WorkerTrace(worker_id)
    trace.mark(stage)
    if cancelled:
        future.cancel()
    elif error is not None:
        future.set_exception(error)
    elif not pending:
        future.set_result(value)
    return future, trace


def worker_payload(rows):
    diagnostic = admission_diagnostic('same_user_one_winner')
    diagnostic['workers'] = rows
    diagnostic['actual'] = diagnostic['outcome_codes'] = [row['outcome'] for row in rows]
    return admission_failure(diagnostic)


@pytest.mark.parametrize('reverse', [False, True])
def test_future_collection_preserves_both_errors_and_worker_identity(reverse):
    from threading import BrokenBarrierError
    pairs = [future_case('left', 'barrier', error=BrokenBarrierError()),
             future_case('right', 'identity', error=m.MigrationSafetyError('migration_server_identity_changed'))]
    if reverse:
        pairs.reverse()
    futures = dict(pairs)
    timed_out = admission_cases.wait_original(futures)
    rows, values = admission_cases.collect_futures(futures, timed_out)
    assert values == {} and timed_out == {}
    assert [(r['worker_id'], r['stage'], r['error']['exception_type']) for r in rows] == [
        ('left', 'barrier', 'BrokenBarrierError'), ('right', 'identity', 'MigrationSafetyError')]
    assert m.validate_result(json.loads(json.dumps(worker_payload(rows))))['facts']['admission']['workers'] == rows


def test_future_collection_keeps_success_beside_error():
    futures = dict([future_case('left', 'done', value=(123, True)),
                    future_case('right', 'begin', error=sa.exc.InvalidRequestError(PASSWORD))])
    rows, values = admission_cases.collect_futures(futures, admission_cases.wait_original(futures))
    assert values == {'left': (123, True)}
    assert rows[0]['status'] == 'completed' and rows[0]['outcome'] == 'created'
    assert rows[1]['status'] == 'failed' and rows[1]['error']['exception_type'] == 'InvalidRequestError'
    assert PASSWORD not in json.dumps(worker_payload(rows))


def test_future_collection_success_preserves_original_values():
    pairs = [future_case('right', 'done', value=(789, False)), future_case('left', 'done', value=(123, True))]
    futures = dict(pairs)
    timeouts = admission_cases.wait_original(futures)
    rows, values = admission_cases.collect_futures(futures, timeouts)
    assert timeouts == {} and all(row['status'] == 'completed' for row in rows)
    assert [values[trace.worker_id] for trace in futures.values()] == [(789, False), (123, True)]
    assert all(row['error'] is None for row in rows)


def test_future_collection_unknown_exception_is_sanitized():
    class PrivateException(Exception):
        pass
    futures = dict([future_case('left', 'connect', error=PrivateException(PASSWORD, URL)),
                    future_case('right', 'done', value=(456, True))])
    rows, _ = admission_cases.collect_futures(futures, admission_cases.wait_original(futures))
    payload = m.validate_result(worker_payload(rows))
    assert rows[0]['error']['exception_type'] == 'other'
    assert not any(secret in json.dumps(payload) for secret in (PASSWORD, URL, 'PrivateException', 'Traceback'))


def test_future_timeout_keeps_peer_result_and_unfinished_state():
    futures = dict([future_case('left', 'barrier', pending=True),
                    future_case('right', 'done', value=(456, True))])
    timeouts = admission_cases.wait_original(futures)
    rows, values = admission_cases.collect_futures(futures, timeouts)
    assert timeouts == {'left': 'barrier'} and values == {'right': (456, True)}
    assert rows[0]['status'] == 'timeout' and rows[0]['finished'] is False
    assert rows[0]['wait_stage'] == 'barrier' and not next(iter(futures)).done()
    assert m.validate_result(worker_payload(rows))['status'] == 'failed'


def test_future_timeout_is_not_reclassified_as_success_after_completion():
    pairs = [future_case('left', 'begin', pending=True), future_case('right', 'done', value=(456, True))]
    futures = dict(pairs)
    timeouts = admission_cases.wait_original(futures)
    pairs[0][0].set_result((123, True))
    pairs[0][1].mark('done')
    rows, values = admission_cases.collect_futures(futures, timeouts)
    assert values['left'] == (123, True)
    assert rows[0]['status'] == 'timeout' and rows[0]['finished'] is True
    assert rows[0]['stage'] == 'done' and rows[0]['wait_stage'] == 'begin'
    assert m.validate_result(worker_payload(rows))['status'] == 'failed'


def test_future_cancelled_and_pending_are_distinct():
    futures = dict([future_case('left', 'login', cancelled=True), future_case('right', 'connect', pending=True)])
    rows, values = admission_cases.collect_futures(futures, admission_cases.wait_original(futures))
    assert values == {}
    assert rows[0]['status'] == 'cancelled' and rows[0]['finished'] is True
    assert rows[1]['status'] == 'pending' and rows[1]['finished'] is False
    assert m.validate_result(worker_payload(rows))['status'] == 'failed'


def test_future_worker_timeout_exception_is_not_collection_timeout():
    futures = dict([future_case('left', 'connect', error=TimeoutError(PASSWORD)),
                    future_case('right', 'done', value=(456, True))])
    timed_out = admission_cases.wait_original(futures)
    rows, _ = admission_cases.collect_futures(futures, timed_out)
    assert timed_out == {} and rows[0]['status'] == 'failed'
    assert rows[0]['error']['exception_type'] == 'TimeoutError'


@pytest.mark.parametrize('pending', [False, True])
def test_future_failure_or_timeout_never_grants_cleanup(catalog, pending):
    run, data = catalog
    futures = dict([future_case('left', 'begin', pending=pending,
                                error=None if pending else RuntimeError(PASSWORD)),
                    future_case('right', 'done', value=(456, True))])
    rows, _ = admission_cases.collect_futures(futures, admission_cases.wait_original(futures))
    assert m.validate_result(worker_payload(rows))['status'] == 'failed'
    run.admission_verified = False
    with pytest.raises(m.MigrationSafetyError, match='migration_admission_not_verified'):
        run.authorize_cleanup()
    assert not run.cleanup_authorized and not data.drops


@pytest.mark.parametrize('mutation', ['id', 'stage', 'type', 'extra', 'missing', 'bool_errno', 'sqlstate'])
def test_future_worker_protocol_is_strict(mutation):
    futures = dict([future_case('left', 'begin', error=ValueError(PASSWORD)),
                    future_case('right', 'done', value=(456, True))])
    rows, _ = admission_cases.collect_futures(futures, {})
    if mutation == 'id':
        rows[0]['worker_id'] = 'unknown'
    elif mutation == 'stage':
        rows[0]['stage'] = URL
    elif mutation == 'type':
        rows[0]['error']['exception_type'] = 'PrivateException'
    elif mutation == 'extra':
        rows[0]['message'] = PASSWORD
    elif mutation == 'missing':
        del rows[0]['finished']
    elif mutation == 'bool_errno':
        rows[0]['error']['mysql_errno'] = True
    else:
        rows[0]['error']['sqlstate'] = PASSWORD
    assert not admission_cases.valid_workers(rows)
    assert worker_payload(rows)['facts'] == {}


def test_original_seventeen_assertion_ids_are_unchanged():
    assert admission_cases.ASSERTIONS == {
        'independent_connections': 'connections', 'same_user_one_winner': 'same_user',
        'global_initial_allowed': 'global', 'global_capacity_one_winner': 'global',
        'duplicate_idempotency': 'duplicate', 'minute_initial_allowed': 'minute',
        'minute_window_one_winner': 'minute', 'minute_window_retry': 'minute',
        'hour_window_initial_limit': 'hour', 'hour_window_one_winner': 'hour', 'hour_window_retry': 'hour',
        'rollback_observed': 'rollback', 'rollback_no_turn': 'rollback', 'rollback_no_active': 'rollback',
        'rollback_retry_allowed': 'rollback', 'gate_released': 'global', 'time_probe_verified': 'time'}


def test_future_timeout_completion_race_still_records_missed_wait():
    from concurrent.futures import Future, TimeoutError as FutureTimeoutError

    class CompletingFuture(Future):
        def result(self, timeout=None):
            if not self.done():
                assert timeout == 40
                self.set_result((123, True))
                raise FutureTimeoutError()
            return super().result(timeout=0)
    future = CompletingFuture()
    trace = admission_cases.WorkerTrace('left')
    trace.mark('commit')
    futures = {future: trace, **dict([future_case('right', 'done', value=(456, True))])}
    timed_out = admission_cases.wait_original(futures)
    rows, _ = admission_cases.collect_futures(futures, timed_out)
    assert rows[0]['status'] == 'timeout' and rows[0]['finished'] is True
    assert m.validate_result(worker_payload(rows))['status'] == 'failed'


def test_future_wait_policy_adds_no_peer_wait_after_first_failure():
    from threading import BrokenBarrierError
    left, a = future_case('left', 'barrier', error=BrokenBarrierError())
    right, b = future_case('right', 'identity', error=m.MigrationSafetyError('migration_server_identity_changed'))
    left, right = Mock(wraps=left), Mock(wraps=right)
    futures = {left: a, right: b}
    timed_out = admission_cases.wait_original(futures)
    rows, _ = admission_cases.collect_futures(futures, timed_out)
    assert len(rows) == 2
    assert [call.kwargs['timeout'] for call in left.result.call_args_list] == [40, 0]
    assert [call.kwargs['timeout'] for call in right.result.call_args_list] == [0]


@pytest.mark.parametrize('errno,state,expected_errno,expected_state', [
    (1216, '23000', 1216, '23000'), (65536, 'invalid', None, None)])
def test_future_database_exception_uses_safe_driver_metadata(errno, state, expected_errno, expected_state):
    error = pymysql.OperationalError(errno, PASSWORD)
    error.sqlstate = state
    exception = sa.exc.OperationalError('private SQL', {'secret': PASSWORD}, error)
    futures = dict([future_case('left', 'commit', error=exception),
                    future_case('right', 'done', value=(456, True))])
    rows, _ = admission_cases.collect_futures(futures, admission_cases.wait_original(futures))
    value = m.validate_result(worker_payload(rows))
    assert rows[0]['error']['mysql_errno'] == expected_errno
    assert rows[0]['error']['sqlstate'] == expected_state
    assert PASSWORD not in json.dumps(value) and 'private SQL' not in json.dumps(value)


@pytest.mark.parametrize('change', ['not_list', 'length', 'row_type', 'not_finished', 'pending_done',
                                    'timeout_stage', 'error_fields', 'error_kind', 'missing_error',
                                    'unfinished_outcome', 'duplicate_id'])
def test_future_worker_state_consistency_rejects_invalid_facts(change):
    futures = dict([future_case('left', 'begin', error=ValueError()),
                    future_case('right', 'done', value=(456, True))])
    rows, _ = admission_cases.collect_futures(futures, {})
    if change == 'not_list':
        rows = tuple(rows)
    elif change == 'length':
        rows.pop()
    elif change == 'row_type':
        rows[0] = None
    elif change == 'not_finished':
        rows[0]['finished'] = False
    elif change == 'pending_done':
        rows[0].update(status='pending', error=None)
    elif change == 'timeout_stage':
        rows[0]['wait_stage'] = 'begin'
    elif change == 'error_fields':
        rows[0]['error']['message'] = PASSWORD
    elif change == 'error_kind':
        rows[0]['error']['failure_kind'] = 'unknown'
    elif change == 'missing_error':
        rows[0]['error'] = None
    elif change == 'unfinished_outcome':
        rows[0].update(status='pending', error=None, finished=False, outcome='created')
    else:
        rows[1]['worker_id'] = 'left'
    assert not admission_cases.valid_workers(rows)


def test_future_trace_rejects_unknown_worker_id():
    with pytest.raises(ValueError, match='invalid_worker_id'):
        admission_cases.WorkerTrace('unknown')


def test_future_actual_worker_stage_on_identity_failure_with_owned_sqlite():
    # Real worker/attempt path on owned file SQLite, not a MySQL compatibility claim.
    from tests.test_database_migrations import FLOW
    from tests.test_migration_validation import child
    child(FLOW + """
from tests.agent_admission_cases import verify_concurrency, AdmissionDiagnosticError
from tests.mysql_migration_ci import MigrationSafetyError
with v.create_temporary_sqlite_target() as target, v.SQLiteBoundary(target):
    app=v.create_minimal_migration_app(target)
    v.run_alembic_upgrade(target,app)
    def reject(connection):
        raise MigrationSafetyError('migration_server_identity_changed')
    with app.app_context():
        try:
            verify_concurrency(app,db.engine,verify_connection=reject)
        except AdmissionDiagnosticError as error:
            workers=error.diagnostic['workers']
            assert {row['worker_id'] for row in workers}=={'left','right'}
            assert all(row['stage']=='identity' and row['status']=='failed' for row in workers)
            assert all(row['error']['exception_type']=='MigrationSafetyError' for row in workers)
        else:
            raise AssertionError('worker failure swallowed')
""")


@pytest.mark.parametrize('system_type', [KeyboardInterrupt, SystemExit, GeneratorExit])
@pytest.mark.parametrize('operation', ['wait', 'collect'])
def test_future_system_exceptions_propagate_unchanged(system_type, operation):
    exception = system_type()
    futures = dict([future_case('left', 'begin', error=exception),
                    future_case('right', 'done', value=(456, True))])
    with pytest.raises(system_type) as raised:
        if operation == 'wait':
            admission_cases.wait_original(futures)
        else:
            admission_cases.collect_futures(futures, {})
    assert raised.value is exception


def test_future_ordinary_unknown_exception_still_collects_both_workers():
    class PrivateFailure(Exception):
        pass
    futures = dict([future_case('left', 'commit', error=PrivateFailure(PASSWORD, URL)),
                    future_case('right', 'done', value=(456, True))])
    rows, values = admission_cases.collect_futures(futures, admission_cases.wait_original(futures))
    assert values == {'right': (456, True)}
    assert rows[0]['status'] == 'failed' and rows[0]['error']['exception_type'] == 'other'
    assert rows[1]['status'] == 'completed' and rows[1]['outcome'] == 'created'
    payload = m.validate_result(worker_payload(rows))
    assert not any(secret in json.dumps(payload) for secret in (PASSWORD, URL, 'PrivateFailure', 'Traceback'))


@pytest.mark.parametrize('failure_point,assertion_id,scenario,codes', [
    ('before_loop', 'same_user_one_winner', 'same_user', ['created', 'created']),
    ('after_first_loop', 'global_capacity_one_winner', 'global', ['created', 'created']),
    ('after_second_loop', 'duplicate_idempotency', 'duplicate', ['created', 'created']),
])
def test_outcome_shadow_real_check_runtime(monkeypatch, catalog, failure_point, assertion_id, scenario, codes):
    """Call the real nested check via controlled races, never copy its implementation."""
    from concurrent.futures import Future
    from app.models import User, Conversation
    run, data = catalog
    application = Flask('outcome-shadow-runtime')
    engine = Mock(dialect=SimpleNamespace(name='mysql'))
    connection = engine.connect.return_value
    connection.__enter__ = Mock(return_value=connection)
    connection.__exit__ = Mock(return_value=False)
    connection.execute.return_value.scalar_one.return_value = 500
    session = Mock()
    session.__enter__ = Mock(return_value=session)
    session.__exit__ = Mock(return_value=False)
    session.begin.return_value.__enter__ = Mock(return_value=session)
    session.begin.return_value.__exit__ = Mock(return_value=False)
    pending, counter = [], [0]

    def flush():
        for row in pending:
            counter[0] += 1
            if isinstance(row, User):
                row.id = counter[0]
            elif isinstance(row, Conversation):
                row.id = format(counter[0], '032x')
        pending.clear()
    session.add_all.side_effect = pending.extend
    session.add.side_effect = pending.append
    session.flush.side_effect = flush
    races = [[True, 'agent_user_busy'], [True, 'agent_global_capacity'], [False, True]]
    index = {'before_loop': 0, 'after_first_loop': 1, 'after_second_loop': 2}[failure_point]
    races[index] = [True, True]
    race_counter = [0]

    class Executor:
        def __init__(self, size):
            assert size == 2
            self.index = race_counter[0]
            race_counter[0] += 1
            self.submitted = 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def submit(self, function, *args, **kwargs):
            future = Future()
            future.set_result((100 + self.submitted, races[self.index][self.submitted]))
            self.submitted += 1
            return future

    precision = time_diagnostic()  # Existing fully validated synthetic MySQL facts.
    assert admission_cases.valid_time_diagnostic(precision)
    monkeypatch.setattr(admission_cases, 'Session', Mock(return_value=session))
    monkeypatch.setattr(admission_cases, 'ThreadPoolExecutor', Executor)
    monkeypatch.setattr(admission_cases, 'mysql_time_probe', Mock(return_value=precision))
    monkeypatch.setattr(admission_cases, 'future_time_probe', Mock(return_value=precision))
    begin = Mock(return_value={'created': True})
    finish = Mock()
    monkeypatch.setattr(admission_cases.c, '_begin_with_session', begin)
    monkeypatch.setattr(admission_cases.c, 'finish_turn', finish)
    with pytest.raises(admission_cases.AdmissionDiagnosticError) as raised:
        admission_cases.verify_concurrency(application, engine)
    diagnostic = raised.value.diagnostic
    assert diagnostic == {'scenario_id': scenario, 'assertion_id': assertion_id,
                          'expected': admission_cases.EXPECTED[assertion_id],
                          'actual': codes, 'outcome_codes': codes, 'time_precision': precision}
    run.stage, run.substage = 'crud', 'admission_concurrency'
    run.state, run.admission_verified = 'HEAD_VERIFIED', False
    payload = m.validate_result(m.safe_failure(raised.value, run))
    assert payload['facts']['admission'] == diagnostic
    assert payload['error_code'] == 'migration_admission_assertion_failed'
    with pytest.raises(m.MigrationSafetyError, match='migration_cleanup_not_authorized'):
        run.authorize_cleanup()
    run.state = 'DATA_AND_DOWNGRADE_VERIFIED'
    with pytest.raises(m.MigrationSafetyError, match='migration_admission_not_verified'):
        run.authorize_cleanup()
    assert not run.cleanup_authorized and not data.drops
    assert begin.call_count == (0 if index == 0 else 1)
    assert finish.call_count == (0, 1, 3)[index]
