"""Connection-free configuration checks for isolated test databases.

Profiles are reviewed code, not expectations derived from a supplied URL.
This module does not create engines, open connections, or manage schemas.
"""

import os
from collections.abc import Mapping, MutableMapping
from types import MappingProxyType

from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError


_MEMORY_URL = 'sqlite:///:memory:'
_MYSQL_TARGETS = MappingProxyType({
    # (host, port, database, username), matching the CI service configuration.
    'github-runner-mysql': ('127.0.0.1', 3306, 'dailydot_test', 'dailydot_test'),
    'github-container-mysql': ('mysql', 3306, 'dailydot_test', 'dailydot_test'),
})


class TestDatabaseSafetyError(RuntimeError):
    """A fixed, credential-free error identifying rejected test configuration."""


def validate_testing_database_config(config: Mapping) -> URL:
    """Validate the final configuration before SQLAlchemy.init_app().

    Reusable for later lifecycle checks. No supplied URL or exception text is
    included in failures, including URL parsing failures.
    """
    if config.get('TESTING') is not True:
        raise TestDatabaseSafetyError('testing_database_requires_testing')
    if config.get('SQLALCHEMY_BINDS'):
        raise TestDatabaseSafetyError('testing_database_binds_not_allowed')
    if config.get('SQLALCHEMY_ENGINE_OPTIONS'):
        raise TestDatabaseSafetyError('testing_database_engine_options_not_allowed')

    raw_url = config.get('SQLALCHEMY_DATABASE_URI')
    if not isinstance(raw_url, (str, URL)) or not raw_url:
        raise TestDatabaseSafetyError('invalid_test_database_url')
    try:
        url = make_url(raw_url)
        target = (url.host, url.port, url.database, url.username)
    except (ArgumentError, TypeError, ValueError):
        raise TestDatabaseSafetyError('invalid_test_database_url') from None

    profile = config.get('TEST_DATABASE_PROFILE', 'sqlite-memory')
    if profile == 'sqlite-memory':
        if url != make_url(_MEMORY_URL):
            raise TestDatabaseSafetyError('invalid_test_database_target')
        return url

    # Local MySQL is deliberately unsupported in this phase. CI indicators
    # are consistency checks, not proof of the remote server's identity.
    if not isinstance(profile, str) or profile not in _MYSQL_TARGETS:
        raise TestDatabaseSafetyError('invalid_test_database_profile')
    if config.get('TEST_DATABASE_ALLOW_MYSQL') != '1':
        raise TestDatabaseSafetyError('testing_mysql_requires_opt_in')
    if config.get('TEST_DATABASE_GITHUB_ACTIONS') != 'true':
        raise TestDatabaseSafetyError('testing_mysql_requires_github_actions')
    if (url.drivername != 'mysql+pymysql' or url.query or not url.password
            or target != _MYSQL_TARGETS[profile]):
        raise TestDatabaseSafetyError('invalid_test_database_target')
    return url


def configure_testing_database(config: MutableMapping,
                               environ: Mapping | None = None) -> None:
    """Read test-only settings on each factory call, then validate them.

    DATABASE_URL is never consulted. An explicitly empty or malformed test URL
    fails closed instead of silently falling back to another database.
    """
    if environ is None:
        environ = os.environ
    config['SQLALCHEMY_DATABASE_URI'] = environ.get('TEST_DATABASE_URL', _MEMORY_URL)
    config['TEST_DATABASE_PROFILE'] = environ.get('TEST_DATABASE_PROFILE', 'sqlite-memory')
    config['TEST_DATABASE_ALLOW_MYSQL'] = environ.get('TEST_DATABASE_ALLOW_MYSQL', '')
    config['TEST_DATABASE_GITHUB_ACTIONS'] = environ.get('GITHUB_ACTIONS', '')
    validate_testing_database_config(config)
