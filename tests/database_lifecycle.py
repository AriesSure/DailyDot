"""The sole schema lifecycle used by test fixtures; never used by the app."""

from contextlib import contextmanager

import pytest
import sqlalchemy as sa
from flask import current_app, has_app_context

from app.testing_database import engine_url_matches_test_database_url, validate_testing_database_config


class DatabaseLifecycleError(RuntimeError):
    """Credential-free lifecycle failure."""


class DatabaseLifecycle:
    def __init__(self, application, database):
        self.application = application
        self.database = database
        self.engine = None
        self.url = None
        self.profile = None
        self.initialized = False
        self.cleanup_authorized = False
        self.closed = False
        self.mysql = False
        self._owned_engines = ()

    def _check_target(self):
        if not has_app_context() or current_app._get_current_object() is not self.application:
            raise DatabaseLifecycleError('test_database_application_changed')
        url = validate_testing_database_config(self.application.config)
        engines = self.database.engines
        if set(engines) != {None}:
            raise DatabaseLifecycleError('test_database_engines_invalid')
        engine = self.database.engine
        engine_url = engine.url
        if not engine_url_matches_test_database_url(engine_url, url):
            raise DatabaseLifecycleError('test_database_engine_target_changed')
        if self.engine is not None and (
            engine is not self.engine or engine_url != self.url
            or self.application.config['TEST_DATABASE_PROFILE'] != self.profile
        ):
            raise DatabaseLifecycleError('test_database_identity_changed')
        return engine, url, engine_url

    def initialize(self):
        if self.engine is not None or self.closed:
            raise DatabaseLifecycleError('test_database_lifecycle_already_used')
        # Mark MySQL attempts fatal even if subsequent configuration validation fails.
        self.mysql = self.application.config.get('TEST_DATABASE_PROFILE') in {
            'github-runner-mysql', 'github-container-mysql',
        }
        try:
            if not has_app_context() or current_app._get_current_object() is not self.application:
                raise DatabaseLifecycleError('test_database_application_changed')
            # Reading engine objects does not connect. Capture ownership first so
            # even invalid configuration can release already allocated resources.
            engines = dict(self.database.engines)
            self._owned_engines = tuple(engines.values())
            engine, _, engine_url = self._check_target()
            self.engine = engine
            self.url = engine_url
            self.profile = self.application.config['TEST_DATABASE_PROFILE']
            if self.mysql:
                inspector = sa.inspect(engine)
                if inspector.get_table_names() or inspector.get_view_names():
                    raise DatabaseLifecycleError('test_database_not_empty')
            # Check again after inspection, immediately before any DDL.
            self._check_target()
            self.database.create_all(bind_key=None)
            self.initialized = True
            self.cleanup_authorized = self.mysql
        except Exception:
            raise DatabaseLifecycleError('test_database_initialization_failed') from None

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            # Remove this app's session before DDL, releasing pending transactions.
            if has_app_context() and current_app._get_current_object() is self.application:
                self.database.session.remove()
            else:
                with self.application.app_context():
                    self.database.session.remove()
            if self.initialized:
                self._check_target()
            if self.mysql and self.initialized:
                if not self.cleanup_authorized:
                    raise DatabaseLifecycleError('test_database_cleanup_not_authorized')
                self._check_target()
                self.database.drop_all(bind_key=None)
        except Exception:
            raise DatabaseLifecycleError('test_database_cleanup_failed') from None
        finally:
            self.cleanup_authorized = False
            dispose_failed = False
            seen = set()
            for engine in self._owned_engines:
                if id(engine) in seen:
                    continue
                seen.add(id(engine))
                try:
                    engine.dispose()
                except Exception:
                    dispose_failed = True
            if dispose_failed:
                raise DatabaseLifecycleError('test_database_dispose_failed') from None


@contextmanager
def isolated_database(application, database):
    """Abort a MySQL run on unsafe setup/cleanup; never reuse a partial schema."""
    lifecycle = DatabaseLifecycle(application, database)
    with application.app_context():
        try:
            lifecycle.initialize()
        except DatabaseLifecycleError:
            release_failed = False
            try:
                lifecycle.close()
            except DatabaseLifecycleError:
                release_failed = True
            if lifecycle.mysql:
                reason = 'unsafe_mysql_test_initialization'
                if release_failed:
                    reason += '_resource_release_failed'
                pytest.exit(reason, returncode=1)
            if release_failed:
                raise DatabaseLifecycleError('test_database_initialization_and_release_failed') from None
            raise
        try:
            yield application
        finally:
            try:
                lifecycle.close()
            except DatabaseLifecycleError:
                if lifecycle.mysql:
                    pytest.exit('unsafe_mysql_test_cleanup', returncode=1)
                raise
