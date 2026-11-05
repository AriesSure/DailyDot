"""Python-level test I/O guards; not an OS sandbox or CI authentication."""

import os
import socket
import sqlite3
import sqlite3.dbapi2

import pymysql
import requests
from flask import current_app, has_app_context
from sqlalchemy.engine import Engine, make_url

from app.testing_database import configure_testing_database, validate_testing_database_config


def mysql_ci_url(environ=None):
    """Require the code-owned runner profile, independently of the supplied URL."""
    environ = os.environ if environ is None else environ
    if environ.get('TEST_DATABASE_PROFILE') != 'github-runner-mysql':
        raise RuntimeError('mysql_ci_requires_runner_profile')
    config = {'TESTING': True}
    configure_testing_database(config, environ)
    return validate_testing_database_config(config)


class NetworkBoundary:
    def __init__(self):
        self.attempts = []

    def deny(self, *args, **kwargs):
        self.attempts.append('forbidden_test_io')
        raise AssertionError('external_io_forbidden')

    def mysql_url(self):
        try:
            return mysql_ci_url()
        except RuntimeError:
            return None

    def tcp_allowed(self, address, sock=None):
        url = self.mysql_url()
        return (
            url is not None and isinstance(address, tuple) and len(address) == 2
            and address == (url.host, url.port)
            and (sock is None or (
                sock.family == socket.AF_INET and sock.type == socket.SOCK_STREAM
                and sock.proto in (0, socket.IPPROTO_TCP)
            ))
        )

    def engine_allowed(self, engine):
        if engine.url == make_url('sqlite:///:memory:'):
            return True
        url = self.mysql_url()
        if url is None or engine.url != url:
            return False
        if has_app_context():
            try:
                return validate_testing_database_config(current_app.config) == url
            except RuntimeError:
                return False
        return True

    def install(self, patch):
        """Install before collection or per test using pytest.MonkeyPatch."""
        original_connect = socket.socket.connect
        original_connect_ex = socket.socket.connect_ex
        original_create = socket.create_connection
        original_engine_connect = Engine.connect
        original_raw = Engine.raw_connection
        original_sqlite = sqlite3.dbapi2.connect
        original_mysql = pymysql.connections.Connection.connect

        def connect(sock, address):
            if not self.tcp_allowed(address, sock):
                return self.deny()
            return original_connect(sock, address)

        def connect_ex(sock, address):
            if not self.tcp_allowed(address, sock):
                return self.deny()
            return original_connect_ex(sock, address)

        def create_connection(address, *args, **kwargs):
            if not self.tcp_allowed(address) or kwargs.get('source_address') is not None:
                return self.deny()
            if len(args) > 1 and args[1] is not None:
                return self.deny()
            return original_create(address, *args, **kwargs)

        def engine_connect(engine, *args, **kwargs):
            if not self.engine_allowed(engine):
                return self.deny()
            return original_engine_connect(engine, *args, **kwargs)

        def raw_connection(engine, *args, **kwargs):
            if not self.engine_allowed(engine):
                return self.deny()
            return original_raw(engine, *args, **kwargs)

        def sqlite_connect(database, *args, **kwargs):
            if database != ':memory:':
                return self.deny()
            return original_sqlite(database, *args, **kwargs)

        def mysql_connect(connection, sock=None):
            url = self.mysql_url()
            expected_password = url.password.encode('latin1') if url else None
            if (url is None or sock is not None
                    or connection.host != url.host or connection.port != url.port
                    or connection.user != url.username or connection.db != url.database
                    or connection.password != expected_password
                    or connection.unix_socket or connection.bind_address):
                return self.deny()
            # Never include a driver's connection exception (or its credentials)
            # in the pytest traceback. A failed connection must still fail.
            try:
                return original_mysql(connection)
            except Exception:
                raise RuntimeError('mysql_ci_connection_failed') from None

        patch.setattr(socket.socket, 'connect', connect)
        patch.setattr(socket.socket, 'connect_ex', connect_ex)
        patch.setattr(socket, 'create_connection', create_connection)
        patch.setattr(socket.socket, 'sendto', self.deny)
        patch.setattr(Engine, 'connect', engine_connect)
        patch.setattr(Engine, 'raw_connection', raw_connection)
        patch.setattr(sqlite3, 'connect', sqlite_connect)
        patch.setattr(sqlite3.dbapi2, 'connect', sqlite_connect)
        patch.setattr(pymysql.connections.Connection, 'connect', mysql_connect)
        patch.setattr(requests.sessions.Session, 'request', self.deny)


def redact_credentials(value):
    """Redact current CI credentials from pytest failures and captured output."""
    value = str(value)
    raw = os.environ.get('TEST_DATABASE_URL', '')
    if raw:
        value = value.replace(raw, '[REDACTED_TEST_DATABASE_URL]')
        try:
            password = make_url(raw).password
        except Exception:
            password = None
        if password:
            value = value.replace(password, '[REDACTED_TEST_PASSWORD]')
    return value


def redact_test_report(report):
    """Keep pytest skip tuples intact, including for -ra and JUnit consumers."""
    if isinstance(report.longrepr, tuple):
        path, line, reason = report.longrepr
        report.longrepr = (redact_credentials(path), line, redact_credentials(reason))
    elif report.longrepr:
        original = str(report.longrepr)
        redacted = redact_credentials(original)
        if redacted != original:
            report.longrepr = redacted
    report.sections = [(name, redact_credentials(value)) for name, value in report.sections]
