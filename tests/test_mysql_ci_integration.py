"""Real MySQL only in the fixed GitHub runner profile; no SQLite fallback."""

import os

import pytest
import sqlalchemy as sa

from app.extensions import db
from app.factory import create_app
from app.models import User, Habit, Record
from app.testing_database import engine_url_matches_test_database_url
from tests.database_lifecycle import isolated_database
from tests.mysql_ci import inspect_server
from tests.network_boundary import mysql_ci_url


pytestmark = pytest.mark.skipif(
    os.environ.get('TEST_DATABASE_PROFILE', 'sqlite-memory') == 'sqlite-memory',
    reason='Real MySQL integration requires the fixed github-runner-mysql CI profile.',
)


@pytest.fixture
def verified_mysql_url():
    # Invalid/non-runner profiles raise: they must never turn into a skipped test.
    return mysql_ci_url()


def test_mysql_fixture_identity_grants_and_crud(verified_mysql_url, app):
    """Exercise the shared app fixture and its normal MySQL schema lifecycle."""
    expected_engine = db.engine  # The app fixture keeps its application context active.
    with app.app_context():
        engine = db.engine
        assert engine is expected_engine
        target_matches = engine_url_matches_test_database_url(engine.url, verified_mysql_url)
        assert target_matches, 'mysql_ci_engine_target_changed'
        database, account, version, scopes = inspect_server(engine)
        assert database == 'dailydot_test'
        assert account == 'dailydot_test@%'
        assert version.startswith('8.0.')
        assert scopes
        inspector = sa.inspect(engine)
        assert set(inspector.get_table_names()) == set(db.metadata.tables)
        assert inspector.get_view_names() == []
        assert {c['name'] for c in inspector.get_columns('records')} >= {
            'id', 'habit_id', 'checkin_date', 'checkin_time',
        }
        user = User(username='mysql-ci-user', email='mysql-ci@example.invalid')
        db.session.add(user)
        db.session.flush()
        habit = Habit(habit_name='MySQL CI', icon='fas fa-check', user_id=user.id)
        db.session.add(habit)
        db.session.flush()
        record = Record(habit_id=habit.id, note='committed')
        db.session.add(record)
        db.session.commit()
        record_id = record.id
        assert db.session.get(Record, record_id).habit.user.id == user.id
        record.note = 'updated'
        db.session.commit()
        db.session.expire_all()
        assert db.session.get(Record, record_id).note == 'updated'
        db.session.add(User(username='rolled-back', email='rollback@example.invalid'))
        db.session.flush()
        db.session.rollback()
        assert db.session.scalar(sa.select(sa.func.count()).select_from(User)) == 1
        db.session.delete(db.session.get(Record, record_id))
        db.session.commit()
        assert db.session.get(Record, record_id) is None


def _close_with_observation(lifecycle, original_close, verified_mysql_url):
    try:
        if lifecycle.initialized:
            target_matches = lifecycle._check_target() == (lifecycle.engine, verified_mysql_url, lifecycle.url)
            assert target_matches, 'mysql_ci_cleanup_target_changed'
            assert lifecycle.database.engine is lifecycle.engine
    finally:
        # The real close owns cleanup authorization and must release resources
        # even if an observation fails or initialization never completed.
        original_close(lifecycle)
    assert lifecycle.cleanup_authorized is False
    assert lifecycle.closed is True


def test_mysql_authorized_cleanup_and_empty_schema(verified_mysql_url, monkeypatch):
    """Observe the existing lifecycle, then verify cleanup using a read-only engine."""
    from tests import database_lifecycle as lifecycle_module

    application = create_app('testing')
    observed = []
    original_initialize = lifecycle_module.DatabaseLifecycle.initialize
    original_close = lifecycle_module.DatabaseLifecycle.close

    def initialize(lifecycle):
        assert lifecycle.cleanup_authorized is False
        original_initialize(lifecycle)
        assert lifecycle.initialized is True
        assert lifecycle.cleanup_authorized is True
        target_matches = lifecycle._check_target() == (lifecycle.engine, verified_mysql_url, lifecycle.url)
        assert target_matches, 'mysql_ci_initialized_target_changed'
        observed.append(lifecycle)

    def close(lifecycle):
        _close_with_observation(lifecycle, original_close, verified_mysql_url)

    monkeypatch.setattr(lifecycle_module.DatabaseLifecycle, 'initialize', initialize)
    monkeypatch.setattr(lifecycle_module.DatabaseLifecycle, 'close', close)
    # No direct create_all/drop_all: use the same context manager as app fixture.
    with isolated_database(application, db):
        assert len(observed) == 1
        assert sa.inspect(db.engine).get_table_names()
    assert observed[0].closed
    verification = sa.create_engine(verified_mysql_url, echo=False, hide_parameters=True)
    try:
        inspect_server(verification, require_empty=True)
        assert sa.inspect(verification).get_table_names() == []
        assert sa.inspect(verification).get_view_names() == []
    finally:
        verification.dispose()


def test_mysql_forward_migration_preserves_legacy_data(verified_mysql_url):
    """Real migrations run only in the fixed CI child; no ordinary app fixture."""
    import json
    from tests.mysql_migration_ci import launch

    result = launch()
    print(json.dumps(result, ensure_ascii=True))
    assert result['status'] == 'passed', 'mysql_forward_migration_failed'