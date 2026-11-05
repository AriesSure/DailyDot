"""Allowlist tests use fake transport functions: never open a MySQL socket."""

import socket
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

import pymysql
import pytest
import requests
from flask import Flask
from sqlalchemy.engine import Engine, make_url

from tests.mysql_ci import REQUIRED_PRIVILEGES, validate_grants
from tests.network_boundary import NetworkBoundary, mysql_ci_url, redact_credentials, redact_test_report


MYSQL = 'mysql+pymysql://dailydot_test:fake-ci-password@127.0.0.1:3306/dailydot_test'
ENV = dict(GITHUB_ACTIONS='true', TEST_DATABASE_PROFILE='github-runner-mysql',
           TEST_DATABASE_ALLOW_MYSQL='1', TEST_DATABASE_URL=MYSQL)
DATABASE_GRANT = (', '.join(sorted(REQUIRED_PRIVILEGES))
                  + r" ON `dailydot\_test`.* TO `dailydot_test`@`%`")


@pytest.mark.parametrize('change', [
    {'GITHUB_ACTIONS': ''}, {'GITHUB_ACTIONS': 'false'},
    {'TEST_DATABASE_PROFILE': 'sqlite-memory'},
    {'TEST_DATABASE_PROFILE': 'github-container-mysql'},
    {'TEST_DATABASE_ALLOW_MYSQL': ''}, {'TEST_DATABASE_URL': 'invalid'},
    *[{'TEST_DATABASE_URL': make_url(MYSQL).set(**change).render_as_string(hide_password=False)}
      for change in ({'host': 'remote.invalid'}, {'host': 'localhost'}, {'host': 'mysql'},
                     {'port': 3307}, {'database': 'other'}, {'username': 'root'},
                     {'drivername': 'mysql'}, {'password': ''}, {'query': {'host': 'other'}})],
])
def test_mysql_profile_rejects_self_authorization(change):
    with pytest.raises(RuntimeError):
        mysql_ci_url(dict(ENV, **change))


def test_fixed_runner_profile_and_generic_url_ignored():
    assert mysql_ci_url(dict(ENV, DATABASE_URL='mysql://root@remote/production')) == make_url(MYSQL)


@pytest.fixture
def guarded(monkeypatch):
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    originals = {name: Mock(return_value=object()) for name in ('connect', 'create', 'ex', 'engine', 'raw', 'mysql', 'sqlite')}
    originals['ex'].return_value = 23  # Prove the actual result is preserved, not a fake success code.
    monkeypatch.setattr(socket.socket, 'connect', originals['connect'])
    monkeypatch.setattr(socket.socket, 'connect_ex', originals['ex'])
    monkeypatch.setattr(socket, 'create_connection', originals['create'])
    monkeypatch.setattr(Engine, 'connect', originals['engine'])
    monkeypatch.setattr(Engine, 'raw_connection', originals['raw'])
    monkeypatch.setattr(pymysql.connections.Connection, 'connect', originals['mysql'])
    monkeypatch.setattr(sqlite3.dbapi2, 'connect', originals['sqlite'])
    boundary = NetworkBoundary()
    boundary.install(monkeypatch)
    return boundary, originals


def fake_socket(family=socket.AF_INET, kind=socket.SOCK_STREAM):
    return SimpleNamespace(family=family, type=kind, proto=0)


def test_only_exact_tcp_endpoint_calls_original(guarded):
    boundary, originals = guarded
    sock = fake_socket()
    target = ('127.0.0.1', 3306)
    socket.socket.connect(sock, target)
    assert socket.socket.connect_ex(sock, target) == 23
    socket.create_connection(target, timeout=1)
    originals['connect'].assert_called_once_with(sock, target)
    originals['ex'].assert_called_once_with(sock, target)
    originals['create'].assert_called_once_with(target, timeout=1)
    assert boundary.attempts == []


@pytest.mark.parametrize('method,address,sock', [
    ('connect', ('127.0.0.1', 3307), fake_socket()),
    ('connect_ex', ('remote.invalid', 3306), fake_socket()),
    ('connect', ('localhost', 3306), fake_socket()),
    ('connect', ('127.0.0.1', 3306), fake_socket(kind=socket.SOCK_DGRAM)),
    ('connect', ('127.0.0.1', 3306), fake_socket(family=socket.AF_INET6)),
    ('sendto', ('127.0.0.1', 3306), fake_socket(kind=socket.SOCK_DGRAM)),
])
def test_other_socket_operations_blocked_and_recorded(guarded, method, address, sock):
    boundary, originals = guarded
    with pytest.raises(AssertionError, match='external_io_forbidden'):
        if method == 'sendto':
            socket.socket.sendto(sock, b'x', address)
        else:
            getattr(socket.socket, method)(sock, address)
    assert boundary.attempts == ['forbidden_test_io']
    for original in originals.values():
        original.assert_not_called()


def test_sqlite_profile_blocks_mysql_even_on_loopback(guarded, monkeypatch):
    boundary, originals = guarded
    monkeypatch.setenv('TEST_DATABASE_PROFILE', 'sqlite-memory')
    for method in ('connect', 'connect_ex'):
        with pytest.raises(AssertionError):
            getattr(socket.socket, method)(fake_socket(), ('127.0.0.1', 3306))
    with pytest.raises(AssertionError):
        socket.create_connection(('127.0.0.1', 3306))
    assert len(boundary.attempts) == 3
    originals['connect'].assert_not_called()
    originals['ex'].assert_not_called()
    originals['create'].assert_not_called()


@pytest.mark.parametrize('url', [MYSQL, 'sqlite:///:memory:'])
def test_engine_connect_and_raw_allow_validated_targets(guarded, url):
    boundary, originals = guarded
    engine = SimpleNamespace(url=make_url(url))
    Engine.connect(engine)
    Engine.raw_connection(engine)
    originals['engine'].assert_called_once_with(engine)
    originals['raw'].assert_called_once_with(engine)
    assert boundary.attempts == []


@pytest.mark.parametrize('url', ['sqlite:///never-open.db', 'mysql+pymysql://root:fake@127.0.0.1:3306/dailydot_test'])
def test_engine_connect_and_raw_reject_untrusted_targets(guarded, url):
    boundary, originals = guarded
    engine = SimpleNamespace(url=make_url(url))
    for operation in (Engine.connect, Engine.raw_connection):
        with pytest.raises(AssertionError):
            operation(engine)
    assert len(boundary.attempts) == 2
    originals['engine'].assert_not_called()
    originals['raw'].assert_not_called()


@pytest.mark.parametrize('change', [
    {'SQLALCHEMY_BINDS': {'other': MYSQL}},
    {'SQLALCHEMY_ENGINE_OPTIONS': {'connect_args': {'host': 'remote.invalid'}}},
    {'SQLALCHEMY_DATABASE_URI': 'mysql+pymysql://root:fake@127.0.0.1:3306/other'},
])
def test_active_app_options_and_binds_cannot_authorize_mysql(guarded, change):
    boundary, originals = guarded
    application = Flask(__name__)
    application.config.update(
        TESTING=True, TEST_DATABASE_PROFILE='github-runner-mysql',
        TEST_DATABASE_ALLOW_MYSQL='1', TEST_DATABASE_GITHUB_ACTIONS='true',
        SQLALCHEMY_DATABASE_URI=MYSQL,
    )
    application.config.update(change)
    with application.app_context(), pytest.raises(AssertionError):
        Engine.connect(SimpleNamespace(url=make_url(MYSQL)))
    assert boundary.attempts == ['forbidden_test_io']
    originals['engine'].assert_not_called()


def fake_mysql(**change):
    values = dict(host='127.0.0.1', port=3306, user='dailydot_test', db='dailydot_test',
                  password=b'fake-ci-password', unix_socket=None, bind_address=None)
    return SimpleNamespace(**dict(values, **change))


def test_direct_dbapi_validated_mysql_only(guarded):
    boundary, originals = guarded
    connection = fake_mysql()
    pymysql.connections.Connection.connect(connection)
    originals['mysql'].assert_called_once_with(connection)
    assert boundary.attempts == []


@pytest.mark.parametrize('change', [
    {'host': 'remote.invalid'}, {'port': 3307}, {'user': 'root'}, {'db': 'other'},
    {'password': b'other'}, {'unix_socket': '/tmp/mysql.sock'}, {'bind_address': '127.0.0.1'},
])
def test_direct_dbapi_bypass_rejected(guarded, change):
    boundary, originals = guarded
    with pytest.raises(AssertionError):
        pymysql.connections.Connection.connect(fake_mysql(**change))
    assert len(boundary.attempts) == 1
    originals['mysql'].assert_not_called()


def test_explicit_dbapi_socket_and_sqlite_file_and_http_rejected(guarded):
    boundary, originals = guarded
    with pytest.raises(AssertionError):
        pymysql.connections.Connection.connect(fake_mysql(), sock=object())
    with pytest.raises(AssertionError):
        sqlite3.connect('never-open.db')
    with pytest.raises(AssertionError):
        requests.Session().request('GET', 'https://example.invalid')
    assert len(boundary.attempts) == 3
    originals['mysql'].assert_not_called()
    originals['sqlite'].assert_not_called()


def test_mysql_connection_failure_does_not_fallback_or_expose_password(guarded):
    _, originals = guarded
    originals['mysql'].side_effect = RuntimeError('fake-ci-password ' + MYSQL)
    with pytest.raises(RuntimeError, match='^mysql_ci_connection_failed$') as caught:
        pymysql.connections.Connection.connect(fake_mysql())
    assert caught.value.__suppress_context__


def test_failure_output_redacts_connection_credentials(monkeypatch):
    monkeypatch.setenv('TEST_DATABASE_URL', MYSQL)
    output = redact_credentials('failure: ' + MYSQL + ' fake-ci-password')
    assert MYSQL not in output
    assert 'fake-ci-password' not in output


def test_report_redaction_preserves_pytest_skip_tuple(monkeypatch):
    monkeypatch.setenv('TEST_DATABASE_URL', MYSQL)
    report = SimpleNamespace(longrepr=('test_module.py', 15, 'Skipped: fake-ci-password'),
                             sections=[('Captured stdout', MYSQL)])
    redact_test_report(report)
    assert isinstance(report.longrepr, tuple)
    assert report.longrepr[:2] == ('test_module.py', 15)
    assert 'fake-ci-password' not in report.longrepr[2]
    assert MYSQL not in report.sections[0][1]


def test_report_keeps_rich_traceback_when_no_credentials_are_present():
    original = SimpleNamespace(description='safe traceback')
    report = SimpleNamespace(longrepr=original, sections=[])
    redact_test_report(report)
    assert report.longrepr is original


def test_expected_database_grants_are_accepted():
    assert len(validate_grants(['GRANT USAGE ON *.* TO `dailydot_test`@`%`', 'GRANT ' + DATABASE_GRANT])) == 2


@pytest.mark.parametrize('grants', [
    [], ['GRANT ALL PRIVILEGES ON *.* TO `dailydot_test`@`%`'],
    ['GRANT ' + DATABASE_GRANT + ' WITH GRANT OPTION'],
    ['GRANT SELECT ON `other`.* TO `dailydot_test`@`%`'],
    ['GRANT ' + DATABASE_GRANT.replace(r'\_', '_')],
    ['GRANT ' + DATABASE_GRANT.replace(r'\_', '%')],
    ['GRANT `admin_role` TO `dailydot_test`@`%`'],
    ['GRANT ALL PRIVILEGES ON `dailydot\\_test`.* TO `dailydot_test`@`%`'],
])
def test_excessive_or_ambiguous_grants_rejected(grants):
    with pytest.raises(RuntimeError):
        validate_grants(grants)
