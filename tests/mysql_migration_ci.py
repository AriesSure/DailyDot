"""Fixed CI-only MySQL migration harness; no application factory or SQLite fallback.

Run only in a dedicated child process. Guards protect ordinary Python I/O, not
hostile code in this process or the OS. No raw child stderr is a public result.
"""

from contextlib import ExitStack, nullcontext, redirect_stderr, redirect_stdout
from contextvars import ContextVar
from datetime import date, datetime
from importlib.metadata import version
import io
import json
import os
from pathlib import Path
from types import MappingProxyType
import re
import socket
import sqlite3
import sqlite3.dbapi2
import subprocess
import sys
from unittest.mock import patch

import pymysql
import requests
import sqlalchemy as sa
from flask import Flask, current_app, has_app_context
from sqlalchemy.pool.base import _ConnectionRecord
from sqlalchemy.dialects import mysql


ROOT = Path(__file__).resolve().parents[1]
INITIAL = 'df8b049a3e7e'
HEAD = '9a097b8e080b'
TABLES = frozenset({'users', 'habits', 'todos', 'records', 'cards', 'alembic_version'})
STATES = ('UNAUTHORIZED', 'TARGET_VALIDATED', 'EMPTY_OWNED', 'INITIAL_VERIFIED',
          'HEAD_VERIFIED', 'DATA_AND_DOWNGRADE_VERIFIED', 'CLEANUP_AUTHORIZED',
          'EMPTY_VERIFIED', 'DISPOSED')
STAGES = ('admission', 'application', 'identity', 'initial', 'history', 'forward',
          'schema', 'crud', 'repeat', 'downgrade', 'cleanup', 'release', 'complete')
CRUD_SUBSTAGES = ('insert_defaults_commit', 'verify_defaults', 'update_commit', 'verify_update',
                 'rollback_probe', 'record_unique', 'card_unique', 'card_foreign_key',
                 'todo_duplicate', 'card_delete', 'card_survivor', 'session_remove',
                 'schema_snapshot', 'data_snapshot')
# Only checks reachable during CRUD or its following Session/snapshot operations.
# bind/dependency/admission/cleanup-only codes are deliberately not public here.
CRUD_ERROR_CODES = (
    'migration_card_probe_unverified',
    'migration_orm_default_failed', 'migration_orm_update_failed', 'migration_orm_rollback_failed',
    'migration_constraint_wrong_error', 'migration_constraint_not_enforced',
    'migration_orm_delete_failed', 'migration_auto_increment_failed',
    'migration_environment_changed', 'migration_application_changed', 'migration_config_changed',
    'migration_engine_changed', 'migration_target_changed', 'migration_creator_changed',
    'migration_physical_path_invalid', 'migration_nested_creator', 'migration_driver_path_invalid',
    'migration_engine_creation_denied', 'migration_driver_denied', 'migration_driver_target_changed',
    'migration_tcp_denied', 'migration_socket_options_denied', 'migration_dns_denied', 'migration_io_denied',
    'migration_unknown_schema_object', 'migration_partition_forbidden', 'migration_unknown_table',
    'migration_index_options_changed', 'migration_foreign_schema', 'migration_foreign_options',
    'migration_unknown_check',
)
GENERAL_ERROR_CODES = ('mysql_forward_migration_failed', 'forward_migration_preflight_failed',
                       'migration_server_identity_changed')
EXCEPTION_TYPES = ('MigrationSafetyError', 'RuntimeError', 'OperationalError', 'IntegrityError',
                   'DBAPIError', 'ProgrammingError', 'CommandError', 'TimeoutError', 'AssertionError', 'OtherError')
DROP_SQL = MappingProxyType({name: 'DROP TABLE `' + name + '`' for name in TABLES})
SELECT_SQL = MappingProxyType({name: 'SELECT * FROM `' + name + '` ORDER BY ' +
              ('version_num' if name == 'alembic_version' else 'id') for name in TABLES})
SYSTEM_ENV = ('SYSTEMROOT', 'WINDIR', 'PATH', 'TEMP', 'TMP', 'LANG', 'LC_ALL')
CI_ENV = ('GITHUB_ACTIONS', 'TEST_DATABASE_PROFILE', 'TEST_DATABASE_ALLOW_MYSQL', 'TEST_DATABASE_URL')
_physical = ContextVar('mysql_migration_physical', default=None)
_driver = ContextVar('mysql_migration_driver', default=None)
_engine_lease = ContextVar('mysql_migration_engine', default=None)


class MigrationSafetyError(RuntimeError):
    def __init__(self, code, *, mysql_errno=None):
        self.code = code
        self.mysql_errno = mysql_errno
        super().__init__(code)


def require(condition, code, *, mysql_errno=None):
    if not condition:
        raise MigrationSafetyError(code, mysql_errno=mysql_errno)


def valid_mysql_errno(value):
    return type(value) is int and 1 <= value <= 65535


def dbapi_mysql_errno(exc):
    # Public SQLAlchemy + PyMySQL exception interfaces only, never message text.
    # Do not walk arbitrary exception chains or carry a previous probe's errno.
    if isinstance(exc, sa.exc.DBAPIError) and isinstance(exc.orig, pymysql.MySQLError):
        args = exc.orig.args
        if args and valid_mysql_errno(args[0]):
            return args[0]
    return None


CARD_FACT_KEYS = ('server_version', 'foreign_key_checks', 'parent_rows', 'residual_rows',
                  'rollback_observed', 'session_removed', 'explicit_rollback_returned')


def valid_card_facts(value):
    if not isinstance(value, dict) or set(value) != set(CARD_FACT_KEYS):
        return False
    version = value['server_version']
    return ((version is None or isinstance(version, str) and re.fullmatch(r'8\.0\.\d{1,3}', version))
            and (value['foreign_key_checks'] is None or type(value['foreign_key_checks']) is int
                 and value['foreign_key_checks'] in (0, 1))
            and all(value[key] is None or type(value[key]) is int and 0 <= value[key] <= 2147483647
                    for key in ('parent_rows', 'residual_rows'))
            and all(value[key] is None or type(value[key]) is bool
                    for key in ('rollback_observed', 'session_removed', 'explicit_rollback_returned')))


def admit(environ=None):
    from tests.network_boundary import mysql_ci_url
    return mysql_ci_url(os.environ if environ is None else environ)


class _ConnectionIdentitySource:
    """Let the unchanged identity helper read the DROP transaction's connection.

    nullcontext neither closes nor commits the outer SQLAlchemy connection.
    Only inspect_server(require_empty=False) uses this narrow adapter.
    """
    def __init__(self, connection):
        self.connection = connection

    def connect(self):
        return nullcontext(self.connection)


class MigrationBoundary:
    """Bind every new physical connection to one pinned Engine/Pool creator.

    Audited SQLAlchemy 2.0.21: _ConnectionRecord.__connect calls
    pool._invoke_creator(record). _creator's setter also replaces that hook;
    both identities are checked. Pool replacement/recreation is fail-closed.
    A creator token alone cannot open TCP: PyMySQL must consume it first.
    """
    def __init__(self, url):
        self.url = url
        self.app = self.db = self.engine = self.pool = None
        self.original_creator = self.original_invoke = self.authorized_creator = None
        self.engine_url = None
        self.attempts = 0
        self.physical_connections = 0
        self.creating = False
        self.stack = ExitStack()

    def deny(self, *args, **kwargs):
        self.attempts += 1
        raise MigrationSafetyError('migration_io_denied')

    def check(self, engine=None):
        from app.testing_database import engine_url_matches_test_database_url
        try:
            current_url = admit()
        except RuntimeError:
            raise MigrationSafetyError('migration_environment_changed') from None
        require(current_url == self.url, 'migration_environment_changed')
        require(has_app_context() and current_app._get_current_object() is self.app,
                'migration_application_changed')
        cfg = self.app.config
        require(cfg.get('TESTING') is False and cfg.get('SQLALCHEMY_DATABASE_URI') == self.url
                and cfg.get('SQLALCHEMY_BINDS') == {} and cfg.get('SQLALCHEMY_ENGINE_OPTIONS') == {},
                'migration_config_changed')
        require(self.app.extensions.get('sqlalchemy') is self.db and set(self.db.engines) == {None}
                and self.db.engine is self.engine and (engine is None or engine is self.engine),
                'migration_engine_changed')
        require(self.engine.pool is self.pool and self.engine.url == self.engine_url
                and engine_url_matches_test_database_url(self.engine.url, self.url),
                'migration_target_changed')
        require(self.pool._creator is self.original_creator
                and self.pool._invoke_creator is self.authorized_creator, 'migration_creator_changed')

    def bind(self, application, database):
        require(self.app is None and self.engine is not None, 'migration_bind_invalid')
        self.app, self.db = application, database
        self.pool = self.engine.pool
        self.engine_url = self.engine.url
        self.original_creator = self.pool._creator
        self.original_invoke = self.pool._invoke_creator

        def authorized_creator(record):
            # Calling this closure directly is not physical-connection authority.
            require(sys._getframe(1).f_code is _ConnectionRecord._ConnectionRecord__connect.__code__
                    and getattr(record, '_ConnectionRecord__pool', None) is self.pool
                    and _engine_lease.get() is self,
                    'migration_physical_path_invalid')
            self.check()
            require(_physical.get() is None, 'migration_nested_creator')
            permit = {'owner': self, 'record': record, 'driver': None, 'tcp_used': False}
            token = _physical.set(permit)
            try:
                result = self.original_invoke(record)
                require(permit['driver'] is result and permit['tcp_used'], 'migration_driver_path_invalid')
                self.physical_connections += 1
                return result
            finally:
                _physical.reset(token)

        self.authorized_creator = authorized_creator
        self.pool._invoke_creator = authorized_creator
        self.check()

    def _permit(self):
        self.check()
        permit = _physical.get()
        if not (permit and permit['owner'] is self and _driver.get() is permit):
            return self.deny()
        return permit

    def __enter__(self):
        require(sa.__version__ == '2.0.21' and version('Flask-SQLAlchemy') == '3.0.5',
                'migration_dependency_version_changed')
        require('_invoke_creator' in _ConnectionRecord._ConnectionRecord__connect.__code__.co_names,
                'migration_pool_contract_changed')
        require(admit() == self.url, 'migration_admission_changed')
        original_init = sa.engine.Engine.__init__
        original_connect, original_raw = sa.engine.Engine.connect, sa.engine.Engine.raw_connection
        original_tcp, original_ex = socket.socket.connect, socket.socket.connect_ex
        original_create = socket.create_connection
        original_mysql = pymysql.connections.Connection.connect

        def engine_init(engine, pool, dialect, url, logging_name=None, echo=None,
                        query_cache_size=500, execution_options=None, hide_parameters=False):
            from app.testing_database import engine_url_matches_test_database_url
            require(self.creating and self.engine is None
                    and engine_url_matches_test_database_url(url, self.url), 'migration_engine_creation_denied')
            original_init(engine, pool, dialect, url, logging_name=logging_name, echo=echo,
                          query_cache_size=query_cache_size, execution_options=execution_options,
                          hide_parameters=hide_parameters)
            self.engine = engine
            engine.hide_parameters = True

        def engine_connect(engine, *args, **kwargs):
            self.check(engine)
            token = _engine_lease.set(self)
            try:
                return original_connect(engine, *args, **kwargs)
            finally:
                _engine_lease.reset(token)

        def raw_connection(engine, *args, **kwargs):
            self.check(engine)
            token = _engine_lease.set(self)
            try:
                return original_raw(engine, *args, **kwargs)
            finally:
                _engine_lease.reset(token)

        def driver_connect(connection, sock=None):
            self.check()
            permit = _physical.get()
            require(permit is not None and permit['owner'] is self and permit['driver'] is None
                    and type(connection) is pymysql.connections.Connection and sock is None,
                    'migration_driver_denied')
            require(connection.host == self.url.host and connection.port == self.url.port
                    and connection.user == self.url.username and connection.db == self.url.database
                    and connection.password == self.url.password.encode('latin1')
                    and not connection.unix_socket and not connection.bind_address
                    and connection.charset == 'utf8mb4', 'migration_driver_target_changed')
            permit['driver'] = connection
            token = _driver.set(permit)
            try:
                return original_mysql(connection)
            finally:
                _driver.reset(token)

        def tcp(sock, address, *, connect_ex=False):
            permit = self._permit()
            require(not permit['tcp_used'] and address == ('127.0.0.1', 3306)
                    and sock.family == socket.AF_INET and sock.type == socket.SOCK_STREAM
                    and sock.proto in (0, socket.IPPROTO_TCP), 'migration_tcp_denied')
            permit['tcp_used'] = True
            return (original_ex if connect_ex else original_tcp)(sock, address)

        def create_connection(address, *args, **kwargs):
            self._permit()
            require(address == ('127.0.0.1', 3306) and len(args) <= 1
                    and not kwargs, 'migration_socket_options_denied')
            return original_create(address, *args)

        def getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
            self._permit()
            require((host, port) == ('127.0.0.1', 3306) and family in (0, socket.AF_INET)
                    and type in (0, socket.SOCK_STREAM) and proto in (0, socket.IPPROTO_TCP)
                    and flags == 0, 'migration_dns_denied')
            # Numeric target only: do not call any resolver, including libc DNS.
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', (host, port))]

        try:
            for name, replacement in (('connect', lambda sock, addr: tcp(sock, addr)),
                                      ('connect_ex', lambda sock, addr: tcp(sock, addr, connect_ex=True)),
                                      ('sendto', self.deny), ('sendmsg', self.deny)):
                if hasattr(socket.socket, name):
                    self.stack.enter_context(patch.object(socket.socket, name, replacement))
            self.stack.enter_context(patch.object(socket, 'create_connection', create_connection))
            self.stack.enter_context(patch.object(socket, 'getaddrinfo', getaddrinfo))
            for name in ('gethostbyname', 'gethostbyname_ex', 'gethostbyaddr', 'getnameinfo'):
                self.stack.enter_context(patch.object(socket, name, self.deny))
            self.stack.enter_context(patch.object(requests.sessions.Session, 'request', self.deny))
            self.stack.enter_context(patch.object(sqlite3, 'connect', self.deny))
            self.stack.enter_context(patch.object(sqlite3.dbapi2, 'connect', self.deny))
            self.stack.enter_context(patch.object(sa.engine.Engine, '__init__', engine_init))
            self.stack.enter_context(patch.object(sa.engine.Engine, 'connect', engine_connect))
            self.stack.enter_context(patch.object(sa.engine.Engine, 'raw_connection', raw_connection))
            self.stack.enter_context(patch.object(pymysql.connections.Connection, 'connect', driver_connect))
            return self
        except BaseException:
            self.stack.close()
            raise

    def __exit__(self, *exc):
        try:
            if (self.pool is not None and self.pool._creator is self.original_creator
                    and self.pool._invoke_creator is self.authorized_creator):
                self.pool._invoke_creator = self.original_invoke
        finally:
            self.stack.close()


def minimal_app(boundary):
    from app.extensions import db, migrate
    # app's package exports its lazy factory; it is never called or imported here.
    application = Flask('dailydot_mysql_migration_ci', static_folder=None)
    application.config.update(TESTING=False, SQLALCHEMY_DATABASE_URI=boundary.url,
                              SQLALCHEMY_TRACK_MODIFICATIONS=False, SQLALCHEMY_ECHO=False)
    boundary.creating = True
    try:
        db.init_app(application)
    finally:
        boundary.creating = False
    migrate.init_app(application, db)
    with application.app_context():
        boundary.bind(application, db)
    return application, db


def object_inventory(connection):
    tables = connection.execute(sa.text(
        'SELECT t.TABLE_NAME,t.TABLE_TYPE,t.ENGINE,t.TABLE_COLLATION,t.CREATE_OPTIONS,c.CHARACTER_SET_NAME '
        'FROM information_schema.TABLES t LEFT JOIN information_schema.COLLATION_CHARACTER_SET_APPLICABILITY c '
        'ON c.COLLATION_NAME=t.TABLE_COLLATION WHERE t.TABLE_SCHEMA=DATABASE()')).all()
    for table, column in (('TRIGGERS', 'TRIGGER_SCHEMA'), ('ROUTINES', 'ROUTINE_SCHEMA'),
                          ('EVENTS', 'EVENT_SCHEMA')):
        require(not connection.execute(sa.text(
            'SELECT 1 FROM information_schema.' + table + ' WHERE ' + column + '=DATABASE()')).first(),
            'migration_unknown_schema_object')
    require(not connection.execute(sa.text(
        'SELECT 1 FROM information_schema.PARTITIONS WHERE TABLE_SCHEMA=DATABASE() '
        'AND PARTITION_NAME IS NOT NULL')).first(), 'migration_partition_forbidden')
    require(all(name in TABLES and kind == 'BASE TABLE' and engine == 'InnoDB' and not options
                and charset == 'utf8mb4'
                for name, kind, engine, collation, options, charset in tables), 'migration_unknown_table')
    return tables


def schema_snapshot(engine):
    with engine.connect() as connection:
        tables = object_inventory(connection)
        inspector = sa.inspect(connection)
        result = {}
        for name, _, storage, collation, _, charset in tables:
            columns = tuple((c['name'], str(c['type'].compile(dialect=connection.dialect)),
                             c['nullable'], c.get('default'), bool(c.get('autoincrement')),
                             bool(c.get('computed') or c.get('identity')))
                            for c in inspector.get_columns(name))
            indexes = tuple(sorted((i['name'], bool(i['unique']), tuple(i['column_names']))
                                   for i in inspector.get_indexes(name)))
            require(all(not i.get('column_sorting') and not any(i.get('dialect_options', {}).values())
                        for i in inspector.get_indexes(name)), 'migration_index_options_changed')
            foreign = []
            foreign_names = []
            for fk in inspector.get_foreign_keys(name):
                require(fk.get('referred_schema') in (None, 'dailydot_test'), 'migration_foreign_schema')
                options = fk.get('options', {})
                require(all(options.get(key) in (None, 'RESTRICT', 'NO ACTION')
                            for key in ('ondelete', 'onupdate'))
                        and not options.get('deferrable') and options.get('initially') is None,
                        'migration_foreign_options')
                foreign.append((tuple(fk['constrained_columns']), fk['referred_table'],
                                tuple(fk['referred_columns'])))
                foreign_names.append(fk['name'])
            require(not inspector.get_check_constraints(name), 'migration_unknown_check')
            result[name] = {'columns': columns, 'indexes': indexes,
                            'pk': tuple(inspector.get_pk_constraint(name)['constrained_columns']),
                            'foreign': tuple(sorted(foreign)),
                            'foreign_names': tuple(sorted(foreign_names)),
                            'unique': tuple(sorted((u['name'], tuple(u['column_names']))
                                                  for u in inspector.get_unique_constraints(name))),
                            'collation': collation, 'charset': charset, 'engine': storage}
        return result


def verify_head(schema, metadata, collation):
    require(set(schema) == TABLES, 'migration_head_tables_mismatch')
    require(set(metadata.tables) == TABLES - {'alembic_version'}, 'migration_model_metadata_incomplete')
    for name, table in metadata.tables.items():
        actual = schema[name]
        columns = {c[0]: c for c in actual['columns']}
        require(set(columns) == set(table.c.keys()), 'migration_head_columns_mismatch')
        for expected in table.c:
            _, kind, nullable, default, autoincrement, generated = columns[expected.name]
            expected_kind = str(expected.type.compile(dialect=mysql.dialect()))
            allowed = {'TINYINT(1)', 'BOOLEAN', 'BOOL'} if isinstance(expected.type, sa.Boolean) else {expected_kind}
            if expected_kind == 'INTEGER':
                allowed.add('INTEGER(11)')
            require(kind in allowed and nullable == expected.nullable and default is None
                    and not generated and autoincrement == (expected.name == 'id'),
                    'migration_head_column_mismatch')
        indexes = {(i.name, bool(i.unique), tuple(c.name for c in i.columns)) for i in table.indexes}
        uniques = {(c.name, tuple(x.name for x in c.columns)) for c in table.constraints
                   if isinstance(c, sa.UniqueConstraint)}
        indexes.update((name, True, cols) for name, cols in uniques)
        unique_indexes = {(name, cols) for name, unique, cols in indexes if unique}
        foreign = {(tuple(f.parent.name for f in c.elements), c.elements[0].column.table.name,
                    tuple(f.column.name for f in c.elements)) for c in table.foreign_key_constraints}
        require(actual['pk'] == ('id',) and set(actual['indexes']) == indexes
                and set(actual['unique']) == unique_indexes and set(actual['foreign']) == foreign
                and len(actual['foreign']) == len(foreign)
                and len(actual['foreign_names']) == len(foreign)
                and all(isinstance(name, str) and name for name in actual['foreign_names'])
                and actual['collation'] == collation and actual['charset'] == 'utf8mb4',
                'migration_head_constraint_mismatch')
    version_table = schema['alembic_version']
    require(version_table['pk'] == ('version_num',) and not version_table['indexes']
            and not version_table['foreign'] and not version_table['unique']
            and not version_table['foreign_names']
            and version_table['columns'] == (('version_num', 'VARCHAR(32)', False, None, False, False),)
            and version_table['collation'] == collation and version_table['charset'] == 'utf8mb4',
            'migration_version_schema_mismatch')


def data_snapshot(engine):
    with engine.connect() as connection:
        return {name: tuple(connection.execute(sa.text(SELECT_SQL[name])).all()) for name in sorted(TABLES)}


def cleanup_order(schema):
    require(set(schema) == TABLES, 'migration_cleanup_objects_mismatch')
    graph = {name: {parent for _, parent, _ in entry['foreign']} for name, entry in schema.items()}
    require(all(parents <= TABLES for parents in graph.values()), 'migration_cleanup_fk_unknown')
    order = []
    # Stable candidates only; dependency edges determine which can be removed.
    priority = ('records', 'todos', 'cards', 'habits', 'users', 'alembic_version')
    while graph:
        leaves = [name for name in priority if name in graph
                  and not any(name in parents for child, parents in graph.items() if child != name)]
        require(bool(leaves), 'migration_cleanup_fk_cycle')
        name = leaves[0]
        order.append(name)
        del graph[name]
    return order


class MigrationRun:
    def __init__(self):
        self.state = STATES[0]
        self.stage = 'admission'
        self.substage = None
        self.cleanup_authorized = False
        self.boundary = self.app = self.db = None
        self.verified_schema = self.verified_data = None
        self.collation = None
        self.facts = {}
        self.server_identity = None
        self.card_probe = None
        self._card_connection = self._card_rollback_listener = None

    def start_card_probe(self):
        """Read from the exact Session connection before adding the invalid row."""
        self.card_probe = dict.fromkeys(CARD_FACT_KEYS)
        try:
            connection = self.db.session.connection()
            self.verify_server_identity(self.boundary.engine, connection=connection)
            version, checks, parents = connection.execute(sa.text(
                'SELECT VERSION(), @@SESSION.foreign_key_checks, '
                '(SELECT COUNT(*) FROM users WHERE id=999)')).one()
            values = {**self.card_probe, 'server_version': version,
                      'foreign_key_checks': checks, 'parent_rows': parents}
            require(valid_card_facts(values), 'migration_card_probe_unverified')
            self.card_probe = values
            require(version == self.server_identity[2] and checks == 1 and parents == 0,
                    'migration_card_probe_unverified')

            def observed(conn):
                # SQLAlchemy emits this BEFORE the DBAPI rollback: not an ACK.
                self.card_probe['rollback_observed'] = True

            sa.event.listen(connection, 'rollback', observed)
            self._card_connection, self._card_rollback_listener = connection, observed
        except MigrationSafetyError:
            raise
        except Exception:
            raise MigrationSafetyError('migration_card_probe_unverified') from None

    def read_card_residue_after_rollback(self):
        """Separate read transaction; never assume the probe's physical connection."""
        if (self.card_probe is None or self.card_probe['rollback_observed'] is not True
                or not (self.card_probe['session_removed'] is True
                        or self.card_probe['explicit_rollback_returned'] is True)):
            return
        try:
            self.boundary.check()
            with self.boundary.engine.connect() as connection:
                connection.begin()  # Read-only transaction; close rolls it back.
                self.verify_server_identity(self.boundary.engine, connection=connection)
                count = connection.execute(sa.text(
                    "SELECT COUNT(*) FROM cards WHERE user_id=999 AND date='2025-01-04'"
                )).scalar_one()
                require(type(count) is int and 0 <= count <= 2147483647,
                        'migration_card_probe_unverified')
            self.card_probe['residual_rows'] = count
        except Exception:
            self.card_probe['residual_rows'] = None  # Never fabricate zero.

    def remove_card_listener(self):
        if self._card_connection is not None:
            try:
                sa.event.remove(self._card_connection, 'rollback', self._card_rollback_listener)
            except Exception:
                pass  # Diagnostic teardown must not replace the primary error.
            finally:
                self._card_connection = self._card_rollback_listener = None

    def advance(self, expected, following):
        if not (self.state == expected and expected in STATES and following in STATES
                and STATES.index(following) == STATES.index(expected) + 1):
            self.cleanup_authorized = False
            raise MigrationSafetyError('migration_state_invalid')
        self.state = following

    def verify_server_identity(self, engine, *, require_empty=False, connection=None):
        """Validate fixed policy and compare to the first immutable identity.

        inspect_server already checks raw grants. Revalidate its returned scopes
        through the SAME validate_grants policy: these descriptions are only
        text, never SQL executed on a database. No separate privilege allowlist.
        """
        from tests.mysql_ci import inspect_server, validate_grants
        try:
            self.boundary.check()
            require(engine is self.boundary.engine, 'migration_server_identity_changed')
            source = engine
            if connection is not None:
                require(not require_empty and connection.engine is engine and not connection.closed
                        and not connection.invalidated and connection.in_transaction(),
                        'migration_server_identity_changed')
                source = _ConnectionIdentitySource(connection)
            database, account, server_version, scopes = inspect_server(source, require_empty=require_empty)
            require(database == 'dailydot_test' and account == 'dailydot_test@%'
                    and isinstance(server_version, str) and re.fullmatch(r'8\.0\.\d{1,3}', server_version)
                    and isinstance(scopes, (list, tuple)) and all(isinstance(scope, str) for scope in scopes),
                    'migration_server_identity_changed')
            grants = ['GRANT USAGE ON *.* TO CURRENT_USER()'
                      if scope == 'USAGE ON *.* (no global business privileges)'
                      else 'GRANT ' + scope + ' TO CURRENT_USER()' for scope in scopes]
            checked_scopes = validate_grants(grants)
            normalized_scopes = []
            for scope in checked_scopes:
                if scope == 'USAGE ON *.* (no global business privileges)':
                    normalized_scopes.append(scope)
                else:
                    privileges, target = scope.split(' ON ', 1)
                    normalized_scopes.append(', '.join(sorted(privileges.split(', '))) + ' ON ' + target)
            identity = (database, account, server_version, tuple(sorted(normalized_scopes)))
            if self.server_identity is None:
                require(self.state == 'TARGET_VALIDATED', 'migration_server_identity_changed')
                self.server_identity = identity
            else:
                require(identity == self.server_identity, 'migration_server_identity_changed')
            return identity
        except Exception:
            self.cleanup_authorized = False
            raise MigrationSafetyError('migration_server_identity_changed') from None

    def recheck_complete(self):
        self.verify_server_identity(self.boundary.engine)
        schema = schema_snapshot(self.boundary.engine)
        verify_head(schema, self.db.metadata, self.collation)
        data = data_snapshot(self.boundary.engine)
        require(data['alembic_version'] == ((HEAD,),) and schema == self.verified_schema
                and data == self.verified_data, 'migration_cleanup_proof_changed')
        return schema, data

    def authorize_cleanup(self):
        self.cleanup_authorized = False
        require(self.state == 'DATA_AND_DOWNGRADE_VERIFIED', 'migration_cleanup_not_authorized')
        self.recheck_complete()
        require(self.boundary.attempts == 0, 'migration_io_attempt_recorded')
        self.advance('DATA_AND_DOWNGRADE_VERIFIED', 'CLEANUP_AUTHORIZED')
        self.cleanup_authorized = True

    def cleanup(self):
        try:
            require(self.state == 'CLEANUP_AUTHORIZED' and self.cleanup_authorized,
                    'migration_cleanup_not_authorized')
            schema, data = self.recheck_complete()
            order = cleanup_order(schema)
            remaining = dict(schema)
            for name in order:
                self.boundary.check()
                require(schema_snapshot(self.boundary.engine) == remaining,
                        'migration_cleanup_inventory_changed')
                with self.boundary.engine.begin() as connection:
                    self.verify_server_identity(self.boundary.engine, connection=connection)
                    # Closed constant mapping: never a user-provided identifier.
                    connection.execute(sa.text(DROP_SQL[name]))
                del remaining[name]
                require(schema_snapshot(self.boundary.engine) == remaining,
                        'migration_cleanup_delete_mismatch')
            require(not schema_snapshot(self.boundary.engine), 'migration_cleanup_not_empty')
            self.advance('CLEANUP_AUTHORIZED', 'EMPTY_VERIFIED')
        finally:
            self.cleanup_authorized = False

    def release(self, primary=None):
        self.cleanup_authorized = False
        errors = []
        if self.app is not None:
            try:
                with self.app.app_context():
                    self.db.session.remove()
                    if self.card_probe is not None:
                        self.card_probe['session_removed'] = True
                        if primary is not None and self.stage == 'crud' and self.substage == 'card_foreign_key':
                            self.read_card_residue_after_rollback()
            except Exception as exc:
                errors.append(exc)
        self.remove_card_listener()
        if self.boundary is not None and self.boundary.engine is not None:
            try:
                owner = self.boundary
                if owner.engine.pool is owner.pool or owner.pool is None:
                    owner.engine.dispose()
                else:
                    owner.pool.dispose()  # Release only the originally owned pool.
            except Exception as exc:
                errors.append(exc)
        if primary is None and errors:
            raise MigrationSafetyError('migration_resource_release_failed') from None
        if primary is None and self.state == 'EMPTY_VERIFIED':
            self.advance('EMPTY_VERIFIED', 'DISPOSED')

    def verify_crud_and_snapshots(self):
        """The original CRUD/Session/snapshot sequence, with fixed diagnostics."""
        self.stage = 'crud'
        verify_crud(self.db, self)
        self.substage = 'session_remove'
        self.db.session.remove()
        self.substage = 'schema_snapshot'
        schema = schema_snapshot(self.boundary.engine)
        self.substage = 'data_snapshot'
        data = data_snapshot(self.boundary.engine)
        self.verified_schema, self.verified_data = schema, data

    def execute(self):
        from alembic import command
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        from tools.migration_validation import _validate_revision_chain
        url = admit()
        _validate_revision_chain()
        self.advance('UNAUTHORIZED', 'TARGET_VALIDATED')
        self.boundary = MigrationBoundary(url)
        with self.boundary:
            primary = None
            try:
                self.stage = 'application'
                self.app, self.db = minimal_app(self.boundary)
                with self.app.app_context():
                    engine = self.boundary.engine
                    self.stage = 'identity'
                    database, account, server_version, _ = self.verify_server_identity(engine, require_empty=True)
                    require(not schema_snapshot(engine), 'migration_initial_database_not_empty')
                    with engine.connect() as connection:
                        charset, collation, client_charset, fk, autocommit = connection.execute(sa.text(
                            'SELECT @@character_set_database,@@collation_database,'
                            '@@character_set_connection,@@foreign_key_checks,@@autocommit')).one()
                    require(charset == client_charset == 'utf8mb4' and fk == 1
                            and re.fullmatch(r'utf8mb4_[a-z0-9_]+', collation),
                            'migration_server_settings_invalid')
                    self.collation = collation
                    self.facts = {'server_version': server_version, 'charset': charset,
                                  'collation': collation, 'foreign_keys': True,
                                  'autocommit': bool(autocommit), 'database': database, 'account': account}
                    self.advance('TARGET_VALIDATED', 'EMPTY_OWNED')
                    config = Config(str(ROOT / 'migrations/alembic.ini'))
                    config.set_main_option('script_location', str(ROOT / 'migrations'))
                    self.stage = 'initial'
                    command.upgrade(config, INITIAL)
                    self.verify_server_identity(engine)
                    with engine.connect() as connection:
                        ScriptDirectory.from_config(config).get_revision(HEAD).module.validate_initial_schema(connection)
                    self.advance('EMPTY_OWNED', 'INITIAL_VERIFIED')
                    self.stage = 'history'
                    seed_history(engine)
                    with engine.connect() as connection:
                        before = {name: tuple(connection.execute(sa.text(SELECT_SQL[name])).all())
                                  for name in ('users', 'habits', 'records', 'todos')}
                    self.stage = 'forward'
                    command.upgrade(config, 'head')
                    self.verify_server_identity(engine)
                    self.stage = 'schema'
                    head_schema = schema_snapshot(engine)
                    verify_head(head_schema, self.db.metadata, collation)
                    self.facts['tables'] = {name: {'charset': entry['charset'], 'collation': entry['collation']}
                                           for name, entry in sorted(head_schema.items())}
                    self.facts['types'] = {name: next(c[1] for c in head_schema[table]['columns'] if c[0] == column)
                        for name, table, column in (('record_date', 'records', 'checkin_date'),
                            ('record_time', 'records', 'checkin_time'), ('todo_completed', 'todos', 'completed'))}
                    after = data_snapshot(engine)
                    require(after['alembic_version'] == ((HEAD,),), 'migration_head_version_mismatch')
                    require(after['users'] == before['users'] and after['habits'] == before['habits'],
                            'migration_history_changed')
                    with engine.connect() as connection:
                        records = tuple(connection.execute(sa.text(
                            'SELECT id,checkin_date,habit_id,note FROM records ORDER BY id')).all())
                        todos = tuple(connection.execute(sa.text(
                            'SELECT id,event,date,time,note,created_at,user_id FROM todos ORDER BY id')).all())
                        require(records == before['records'] and todos == before['todos']
                                and connection.execute(sa.text('SELECT count(*) FROM records WHERE checkin_time IS NOT NULL')).scalar_one() == 0
                                and connection.execute(sa.text('SELECT count(*) FROM todos WHERE completed<>0')).scalar_one() == 0,
                                'migration_history_changed')
                    self.advance('INITIAL_VERIFIED', 'HEAD_VERIFIED')
                    self.verify_crud_and_snapshots()
                    self.substage = None
                    self.stage = 'repeat'
                    command.upgrade(config, 'head')
                    self.verify_server_identity(engine)
                    require(schema_snapshot(engine) == self.verified_schema
                            and data_snapshot(engine) == self.verified_data, 'migration_repeat_changed_database')
                    self.stage = 'downgrade'
                    statements = []
                    def observe(conn, cursor, statement, parameters, context, executemany):
                        statements.append(statement)
                    sa.event.listen(engine, 'before_cursor_execute', observe)
                    try:
                        try:
                            command.downgrade(config, INITIAL)
                        except RuntimeError as exc:
                            require(str(exc) == 'forward_migration_downgrade_requires_backup_restore',
                                    'migration_downgrade_wrong_error')
                        else:
                            raise MigrationSafetyError('migration_downgrade_accepted')
                    finally:
                        sa.event.remove(engine, 'before_cursor_execute', observe)
                    require(not any(re.match(r'\s*(CREATE|ALTER|DROP|RENAME|TRUNCATE)\b', s, re.I)
                                    for s in statements), 'migration_downgrade_ddl')
                    require(schema_snapshot(engine) == self.verified_schema
                            and data_snapshot(engine) == self.verified_data, 'migration_downgrade_changed_database')
                    self.advance('HEAD_VERIFIED', 'DATA_AND_DOWNGRADE_VERIFIED')
                    self.stage = 'cleanup'
                    self.authorize_cleanup()
                    self.cleanup()
            except BaseException as exc:
                primary = exc
                raise
            finally:
                if primary is None:
                    self.stage = 'release'
                self.release(primary)
        self.stage = 'complete'
        return {'status': 'passed', 'stage': self.stage, 'substage': None, 'error_code': None, 'exception_type': None,
                'mysql_errno': None, 'state': self.state, 'facts': self.facts}


def seed_history(engine):
    statements = (
        "INSERT INTO users VALUES(1,'legacy-one','one@example.invalid',NULL),(2,'legacy-two','two@example.invalid',NULL)",
        "INSERT INTO habits VALUES(11,'read','fas fa-book',NULL,NULL,'习惯 & <note>',1),(12,'run','fas fa-star',NULL,NULL,NULL,2)",
        "INSERT INTO records VALUES(21,'2024-12-31',11,'历史 < & note'),(22,'2025-01-01',11,NULL),(23,'2025-01-01',12,'另一用户')",
        "INSERT INTO todos VALUES(31,'book','31/12/2024','09:00','待办 & note','2024-12-30 09:00:00',1),(32,'run','01/01/2025','10:00',NULL,'2024-12-31 10:00:00',2)",
    )
    with engine.begin() as connection:
        for statement in statements:
            connection.execute(sa.text(statement))


def verify_crud(db, run):
    run.substage = 'insert_defaults_commit'
    from app.models import Card, Record, Todo
    record = Record(habit_id=11, checkin_date=date(2025, 1, 2), note='新增打卡')
    todo = Todo(user_id=2, event='book', date='02/01/2025', time='12:00')
    card = Card(user_id=1, date=date(2025, 1, 2), quote='中文卡片 < & >')
    db.session.add_all([record, todo, card])
    db.session.commit()
    run.substage = 'verify_defaults'
    require(record.checkin_time is not None and todo.completed is False and card.id > 0
            and card.streak == card.habits_completed == 0, 'migration_orm_default_failed')
    run.substage = 'update_commit'
    card.quote = 'updated 中文'
    todo.completed = True
    db.session.commit()
    db.session.expire_all()
    run.substage = 'verify_update'
    require(db.session.get(Card, card.id).quote == 'updated 中文' and db.session.get(Todo, todo.id).completed,
            'migration_orm_update_failed')
    run.substage = 'rollback_probe'
    db.session.add(Card(user_id=1, date=date(2025, 1, 3)))
    db.session.flush()
    db.session.rollback()
    require(db.session.query(Card).count() == 1, 'migration_orm_rollback_failed')
    for invalid, expected_errno, substage in (
            (Record(habit_id=11, checkin_date=date(2025, 1, 2)), 1062, 'record_unique'),
            (Card(user_id=1, date=date(2025, 1, 2)), 1062, 'card_unique'),
            (Card(user_id=999, date=date(2025, 1, 4)), 1452, 'card_foreign_key')):
        run.substage = substage
        if substage == 'card_foreign_key':
            run.start_card_probe()
        db.session.add(invalid)
        try:
            db.session.commit()
        except sa.exc.IntegrityError as exc:
            require(type(exc.orig.args[0]) is int and exc.orig.args[0] == expected_errno,
                    'migration_constraint_wrong_error', mysql_errno=dbapi_mysql_errno(exc))
            db.session.rollback()
            if substage == 'card_foreign_key':
                run.card_probe['explicit_rollback_returned'] = True
                run.read_card_residue_after_rollback()
        else:
            raise MigrationSafetyError('migration_constraint_not_enforced')
    run.substage = 'todo_duplicate'
    db.session.add(Todo(user_id=1, event='book', date='03/01/2025', time='12:00'))
    db.session.commit()
    run.substage = 'card_delete'
    db.session.delete(db.session.get(Card, card.id))
    db.session.commit()
    require(db.session.query(Card).count() == 0, 'migration_orm_delete_failed')
    # Keep a real card across repeat upgrade and refused downgrade.
    run.substage = 'card_survivor'
    survivor = Card(user_id=2, date=date(2025, 1, 2), quote='must survive')
    db.session.add(survivor)
    db.session.commit()
    require(survivor.id > card.id, 'migration_auto_increment_failed')


def safe_failure(exc, run):
    # No str(exc), repr(exc), SQL, parameters, traceback or URL is exported.
    stage = run.stage if isinstance(run.stage, str) and run.stage in STAGES else 'admission'
    substage = run.substage if stage == 'crud' and isinstance(run.substage, str) and run.substage in CRUD_SUBSTAGES else None
    errno = dbapi_mysql_errno(exc)
    code = 'mysql_forward_migration_failed'
    if type(exc) is RuntimeError and exc.args == ('forward_migration_preflight_failed',):
        code = 'forward_migration_preflight_failed'
    if isinstance(exc, MigrationSafetyError):
        if exc.code == 'migration_server_identity_changed' or stage == 'crud' and exc.code in CRUD_ERROR_CODES:
            code = exc.code
        if code == 'migration_constraint_wrong_error' and valid_mysql_errno(exc.mysql_errno):
            errno = exc.mysql_errno
    facts = {}
    if stage == 'crud' and substage == 'card_foreign_key' and valid_card_facts(run.card_probe):
        facts = {'card_foreign_key': dict(run.card_probe)}
    return {'status': 'failed', 'stage': stage, 'substage': substage,
            'error_code': code,
            'exception_type': type(exc).__name__ if type(exc).__name__ in EXCEPTION_TYPES else 'OtherError',
            'mysql_errno': errno,
            'state': run.state if isinstance(run.state, str) and run.state in STATES else 'UNAUTHORIZED', 'facts': facts}


def validate_result(value):
    require(isinstance(value, dict) and set(value) == {'status', 'stage', 'substage', 'error_code', 'exception_type',
                                                     'mysql_errno', 'state', 'facts'}, 'migration_child_protocol_invalid')
    require(value['status'] in ('passed', 'failed') and value['stage'] in STAGES and value['state'] in STATES,
            'migration_child_protocol_invalid')
    require((value['substage'] is None or value['stage'] == 'crud' and value['substage'] in CRUD_SUBSTAGES)
            and value['error_code'] in (None,) + GENERAL_ERROR_CODES + (CRUD_ERROR_CODES if value['stage'] == 'crud' else ())
            and value['exception_type'] in (None,) + EXCEPTION_TYPES
            and (value['mysql_errno'] is None or valid_mysql_errno(value['mysql_errno'])),
            'migration_child_protocol_invalid')
    facts = value['facts']
    require(isinstance(facts, dict), 'migration_child_protocol_invalid')
    if value['status'] == 'passed':
        require(value['stage'] == 'complete' and value['state'] == 'DISPOSED'
                and value['substage'] is value['error_code'] is value['exception_type'] is value['mysql_errno'] is None
                and set(facts) == {'server_version', 'charset', 'collation', 'foreign_keys', 'autocommit',
                                   'database', 'account', 'tables', 'types'}
                and isinstance(facts['server_version'], str)
                and re.fullmatch(r'8\.0\.\d{1,3}', facts['server_version'])
                and facts['charset'] == 'utf8mb4' and isinstance(facts['collation'], str)
                and re.fullmatch(r'utf8mb4_[a-z0-9_]+', facts['collation'])
                and facts['foreign_keys'] is True and type(facts['autocommit']) is bool
                and facts['database'] == 'dailydot_test' and facts['account'] == 'dailydot_test@%'
                and isinstance(facts['tables'], dict) and set(facts['tables']) == TABLES
                and all(isinstance(entry, dict) and set(entry) == {'charset', 'collation'}
                        and entry['charset'] == 'utf8mb4' and entry['collation'] == facts['collation']
                        for entry in facts['tables'].values())
                and isinstance(facts['types'], dict) and set(facts['types']) == {'record_date', 'record_time', 'todo_completed'}
                and facts['types']['record_date'] == 'DATE' and facts['types']['record_time'] == 'TIME'
                and facts['types']['todo_completed'] in ('TINYINT(1)', 'BOOLEAN', 'BOOL'),
                'migration_child_protocol_invalid')
    else:
        require((not facts or value['stage'] == 'crud' and value['substage'] == 'card_foreign_key'
                 and set(facts) == {'card_foreign_key'} and valid_card_facts(facts['card_foreign_key']))
                and value['error_code'] is not None
                and value['exception_type'] is not None, 'migration_child_protocol_invalid')
    return value


def launch():
    url = admit()  # Parent validates too; the child independently repeats admission.
    environ = {key: os.environ[key] for key in SYSTEM_ENV + CI_ENV if key in os.environ}
    try:
        result = subprocess.run([sys.executable, '-I', '-B', str(Path(__file__).resolve())], cwd=ROOT,
                                env=environ, capture_output=True, text=True, timeout=240)
    except subprocess.TimeoutExpired:
        raise MigrationSafetyError('migration_child_timeout') from None
    try:
        require(len(result.stdout) <= 4096, 'migration_child_protocol_invalid')
        payload = validate_result(json.loads(result.stdout))
        encoded = json.dumps(payload)
        require(url.password not in encoded and str(url) not in encoded,
                'migration_child_protocol_invalid')
        require((result.returncode == 0) == (payload['status'] == 'passed'), 'migration_child_exit_mismatch')
    except (ValueError, TypeError, KeyError):
        raise MigrationSafetyError('migration_child_protocol_invalid') from None
    # Never display stderr, even when the child crashes before its safe main.
    return payload


def main():
    run = MigrationRun()
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        try:
            require(len(sys.argv) == 1, 'migration_command_arguments_forbidden')
            result = run.execute()
        except BaseException as exc:
            result = safe_failure(exc, run)
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result['status'] == 'passed' else 1


if __name__ == '__main__':
    sys.path.insert(0, str(ROOT))
    sys.exit(main())
