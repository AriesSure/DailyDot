"""Exercise the dedicated verifier in subprocesses without relaxing pytest I/O guards."""

import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


ROOT = Path(__file__).resolve().parents[1]
BOOT = """
from tools import migration_validation as v
from unittest.mock import Mock, patch
from alembic import command
import sqlite3
"""

SEED = """
c.execute("INSERT INTO users VALUES (1,'synthetic','synthetic@example.invalid',NULL)")
c.execute("INSERT INTO habits VALUES (1,'read','fas fa-book',NULL,NULL,NULL,1)")
c.execute("INSERT INTO todos VALUES (1,'book','01/01/2025','12:00',NULL,'2025-01-01 12:00:00',1)")
c.execute("INSERT INTO records VALUES (1,'2025-01-01',1,'synthetic note')")
c.commit()
"""


def child(source, *, environment=None):
    env = {name: os.environ[name] for name in ("SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP")
           if name in os.environ}
    env.update(environment or {})
    code = f"import sys; sys.path.insert(0, {str(ROOT)!r})\n" + textwrap.dedent(source)
    result = subprocess.run([sys.executable, "-I", "-B", "-c", code], cwd=ROOT,
                            env=env, capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
    return result


def test_real_fresh_and_repeated_upgrade_preserve_data():
    child(BOOT + """
with v.create_temporary_sqlite_target() as t:
    owned_path = t.path
    with v.SQLiteBoundary(t):
        assert v.admit_migration_target(t) == 'EMPTY'
        app = v.create_minimal_migration_app(t)
        with patch.object(command, 'upgrade', wraps=command.upgrade) as upgrade:
            v.run_alembic_upgrade(t, app)
            assert upgrade.call_count == 1
            assert v.admit_migration_target(t) == 'INITIAL'
            with sqlite3.connect(str(t.path)) as c:
""" + textwrap.indent(SEED, "                ") + """
                before = tuple(c.iterdump())
            v.run_alembic_upgrade(t, app)
            assert upgrade.call_count == 2
            with sqlite3.connect(str(t.path)) as c:
                assert tuple(c.iterdump()) == before
                assert c.execute("SELECT version_num FROM alembic_version").fetchall() == [(v.REVISION,)]
                assert not c.execute("SELECT name FROM sqlite_master WHERE name='cards'").fetchall()
assert not owned_path.exists()
""")


CORRUPTIONS = [
    "DROP TABLE records",
    "ALTER TABLE records DROP COLUMN note",
    "DROP INDEX ix_todos_event",
    "DROP INDEX ix_todos_event; CREATE INDEX ix_todos_event ON todos(event)",
    "CREATE TABLE cards(id INTEGER)",
    "ALTER TABLE users ADD COLUMN unexpected TEXT",
    "CREATE INDEX extra_index ON habits(icon)",
    "CREATE VIEW extra_view AS SELECT id FROM users",
    "CREATE TRIGGER extra_trigger AFTER INSERT ON users BEGIN SELECT 1; END",
    "UPDATE alembic_version SET version_num='unknown'",
    "INSERT INTO alembic_version VALUES ('second')",
    "DELETE FROM alembic_version",
    "DROP TABLE alembic_version",
    "ALTER TABLE records RENAME COLUMN datetime TO checkin_date",
    "CREATE TABLE _alembic_tmp_records(id INTEGER)",
    "UPDATE records SET datetime='not-a-date'",
    "UPDATE records SET datetime='2025-02-30'",
    "PRAGMA foreign_keys=OFF; UPDATE records SET habit_id=999",
    "PRAGMA foreign_keys=OFF; UPDATE habits SET user_id=999",
    "PRAGMA foreign_keys=OFF; UPDATE todos SET user_id=999",
    "ALTER TABLE todos RENAME COLUMN note TO original_note; ALTER TABLE todos ADD COLUMN note INTEGER",
    "ALTER TABLE records RENAME TO old_records; CREATE TABLE records (id INTEGER NOT NULL PRIMARY KEY, "
    "datetime DATE NOT NULL,habit_id INTEGER NOT NULL,note VARCHAR(1024),"
    "FOREIGN KEY(habit_id) REFERENCES habits(id)); CREATE INDEX idx ON records(habit_id);"
    "INSERT INTO records VALUES (1,'2025-01-01',1,NULL),(2,'2025-01-01',1,NULL)",
]


@pytest.mark.parametrize("corruption", CORRUPTIONS)
def test_initial_unknown_or_corrupt_state_never_calls_alembic(corruption):
    child(BOOT + """
with v.create_temporary_sqlite_target() as t:
    with v.SQLiteBoundary(t):
        app = v.create_minimal_migration_app(t)
        v.run_alembic_upgrade(t, app)
        with sqlite3.connect(str(t.path)) as c:
""" + textwrap.indent(SEED, "            ") + f"""
            c.executescript({corruption!r})
            c.commit()
            before = tuple(c.iterdump())
        with patch.object(command, 'upgrade', side_effect=AssertionError('must_not_upgrade')) as upgrade:
            try:
                v.run_alembic_upgrade(t, app)
            except v.MigrationValidationError:
                pass
            else:
                raise AssertionError('unsafe admission accepted')
            upgrade.assert_not_called()
        with sqlite3.connect(str(t.path)) as c:
            assert tuple(c.iterdump()) == before
""")


@pytest.mark.parametrize("ddl", [
    "CREATE TABLE users(id INTEGER)",
    "CREATE TABLE users(id INTEGER); CREATE TABLE habits(id INTEGER)",
    "CREATE TABLE alembic_version(version_num VARCHAR(32) NOT NULL PRIMARY KEY)",
])
def test_partial_unversioned_database_rejected_before_app_initialization(ddl):
    child(BOOT + f"""
with v.create_temporary_sqlite_target() as t:
    with v.SQLiteBoundary(t):
        with sqlite3.connect(str(t.path)) as c:
            c.executescript({ddl!r})
            before = tuple(c.iterdump())
        with patch.object(command, 'upgrade') as upgrade:
            try:
                v.create_minimal_migration_app(t)
            except v.MigrationValidationError:
                pass
            else:
                raise AssertionError('partial accepted')
            assert v._OWNED[t]['engine'] is None
            upgrade.assert_not_called()
        with sqlite3.connect(str(t.path)) as c:
            assert tuple(c.iterdump()) == before
""")


@pytest.mark.parametrize("argument", [
    "--database-url", "--sqlite-path", "--db-url", "--revision", "--command", "--creator",
])
def test_cli_rejects_all_arguments(argument):
    result = subprocess.run([sys.executable, "-I", "-B", str(ROOT / "tools" / "verify_migrations.py"), argument],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 2
    assert result.stdout.strip() == "migration_validation_arguments_forbidden"


def test_import_is_inert_and_real_entry_ignores_database_environment(tmp_path):
    sentinel = tmp_path / "never-open.sqlite3"
    sentinel.write_bytes(b"untouched synthetic file")
    env = {name: "sqlite:///" + str(sentinel) for name in (
        "DATABASE_URL", "TEST_DATABASE_URL", "SQLALCHEMY_DATABASE_URI",
    )}
    env.update(FLASK_APP="untrusted:app", SECRET_KEY="test-secret-never-print")
    result = child("""
import socket, sqlite3, sys
from unittest.mock import patch
def deny(*args, **kwargs):
    raise AssertionError('import side effect')
with patch.object(sqlite3,'connect',deny), patch.object(sqlite3.dbapi2,'connect',deny), \
     patch.object(socket.socket,'connect',deny):
    from tools import migration_validation as v
    assert 'app' not in sys.modules
from tools.verify_migrations import main
assert main() == 0
import app
assert app._default_app is None
assert not any(name.startswith('app.blueprints.') for name in sys.modules)
""", environment=env)
    assert "test-secret-never-print" not in result.stdout + result.stderr
    assert str(sentinel) not in result.stdout + result.stderr
    assert sentinel.read_bytes() == b"untouched synthetic file"


def test_forbidden_io_attach_and_unowned_database_fail_closed():
    child(BOOT + """
import socket, requests, sqlalchemy as sa, tempfile
from pathlib import Path
with v.create_temporary_sqlite_target() as t:
    boundary = v.SQLiteBoundary(t)
    with boundary:
        for action in (
            lambda: socket.getaddrinfo('example.invalid',443),
            lambda: socket.create_connection(('127.0.0.1',3306)),
            lambda: socket.socket().connect_ex(('127.0.0.1',3306)),
            lambda: socket.socket(type=socket.SOCK_DGRAM).sendto(b'x',('127.0.0.1',1)),
            lambda: requests.get('https://example.invalid'),
            lambda: socket.socket().connect(('127.0.0.1',3306)),
            lambda: sqlite3.Connection('unowned-file.sqlite3'),
        ):
            try:
                action()
            except v.MigrationValidationError:
                pass
            else:
                raise AssertionError('I/O allowed')
        assert len(boundary.attempts) == 7
        boundary.attempts.clear()  # Expected refusals only, no real network.
        try:
            sqlite3.connect(':memory:')
        except v.MigrationValidationError:
            pass
        else:
            raise AssertionError('unowned database allowed')
        try:
            sa.create_engine('sqlite:///:memory:')
        except v.MigrationValidationError:
            pass
        else:
            raise AssertionError('premature engine allowed')
        with sqlite3.connect(str(t.path)) as c:
            try:
                c.execute("ATTACH DATABASE ':memory:' AS extra")
            except sqlite3.DatabaseError:
                pass
            else:
                raise AssertionError('attach allowed')
""")


def test_engine_and_application_configuration_are_revalidated():
    child(BOOT + """
with v.create_temporary_sqlite_target() as t:
    with v.SQLiteBoundary(t):
        app = v.create_minimal_migration_app(t)
        for key, value in (
            ('SQLALCHEMY_DATABASE_URI','sqlite:///:memory:'),
            ('SQLALCHEMY_BINDS',{'other':'sqlite:///:memory:'}),
            ('SQLALCHEMY_ENGINE_OPTIONS',{'creator': lambda: None}),
        ):
            old = app.config.get(key)
            app.config[key] = value
            with patch.object(command,'upgrade') as upgrade:
                try:
                    v.run_alembic_upgrade(t,app)
                except v.MigrationValidationError:
                    pass
                else:
                    raise AssertionError('changed config accepted')
                upgrade.assert_not_called()
            app.config[key] = old
""")


def test_target_replacement_and_hardlinks_are_rejected():
    child(BOOT + """
import os
with v.create_temporary_sqlite_target() as t:
    original = t.path.with_name('original')
    t.path.rename(original)
    t.path.touch()
    try:
        v.validate_sqlite_target_identity(t)
    except v.MigrationValidationError:
        pass
    else:
        raise AssertionError('replacement accepted')
    t.path.unlink()
    original.rename(t.path)
    link = t.path.with_name('hardlink')
    os.link(t.path,link)
    try:
        v.validate_sqlite_target_identity(t)
    except v.MigrationValidationError:
        pass
    else:
        raise AssertionError('hardlink accepted')
    link.unlink()
""")


def test_default_factory_and_orm_ddl_are_never_called():
    child("""
import sqlalchemy as sa
from flask_sqlalchemy import SQLAlchemy
from unittest.mock import patch
from tools import migration_validation as v
def deny(*args, **kwargs):
    raise AssertionError('unexpected application DDL')
with patch.object(SQLAlchemy,'create_all',deny), patch.object(SQLAlchemy,'drop_all',deny), \
     patch.object(sa.MetaData,'create_all',deny), patch.object(sa.MetaData,'drop_all',deny), \
     patch('dotenv.load_dotenv',deny):
    import app
    with patch('app.factory.create_app',deny), patch.object(app,'_get_default_app',deny):
        v.verify_migrations()
        assert app._default_app is None
""")


def test_admission_connections_are_read_only():
    child(BOOT + """
with v.create_temporary_sqlite_target() as t:
    with v.SQLiteBoundary(t):
        with v._connection(t) as c:
            try:
                c.execute("CREATE TABLE forbidden(id INTEGER)")
            except sqlite3.DatabaseError:
                pass
            else:
                raise AssertionError('admission connection permitted DDL')
        assert v.admit_migration_target(t) == 'EMPTY'
""")


def test_unknown_revision_chain_is_rejected_before_engine_creation():
    child(BOOT + """
from pathlib import Path
from tempfile import TemporaryDirectory
with TemporaryDirectory() as folder:
    versions = Path(folder) / 'migrations' / 'versions'
    versions.mkdir(parents=True)
    (versions / 'df8b049a3e7e_initial.py').write_bytes(
        (v.ROOT / 'migrations' / 'versions' / 'df8b049a3e7e_initial.py').read_bytes())
    (versions / 'unapproved.py').write_text("revision = 'unapproved'")
    with v.create_temporary_sqlite_target() as t:
        with v.SQLiteBoundary(t), patch.object(v,'ROOT',Path(folder)), patch.object(command,'upgrade') as upgrade:
            try:
                v.create_minimal_migration_app(t)
            except v.MigrationValidationError as exc:
                assert str(exc) == 'revision_chain_not_approved'
            else:
                raise AssertionError('unknown revision accepted')
            assert v._OWNED[t]['engine'] is None
            upgrade.assert_not_called()
""")


@pytest.mark.parametrize("change", ["url", "creator", "identity"])
def test_changed_engine_cannot_reach_alembic_or_raw_connection(change):
    child(BOOT + f"""
import sqlalchemy as sa
from app.extensions import db
with v.create_temporary_sqlite_target() as t:
    with v.SQLiteBoundary(t):
        app = v.create_minimal_migration_app(t)
        engine = v._OWNED[t]['engine']
        old_url, old_creator = engine.url, engine.pool._creator
        with app.app_context():
            if {change!r} == 'url':
                engine.url = sa.engine.make_url('sqlite:///:memory:')
            elif {change!r} == 'creator':
                engine.pool._creator = lambda: None
            else:
                db.engines[None] = object()
            with patch.object(command,'upgrade') as upgrade:
                try:
                    v.run_alembic_upgrade(t,app)
                except v.MigrationValidationError:
                    pass
                else:
                    raise AssertionError('engine change accepted')
                upgrade.assert_not_called()
            if {change!r} != 'identity':
                try:
                    engine.raw_connection()
                except v.MigrationValidationError:
                    pass
                else:
                    raise AssertionError('raw connection accepted')
            db.engines[None] = engine
            engine.url, engine.pool._creator = old_url, old_creator
""")


@pytest.mark.parametrize("flag", ["symlink", "reparse"])
def test_link_metadata_is_rejected_without_connecting(flag):
    child(BOOT + f"""
from types import SimpleNamespace
import stat
with v.create_temporary_sqlite_target() as t:
    original = t.path.lstat()
    attributes = {{name:getattr(original,name) for name in ('st_mode','st_dev','st_ino','st_nlink')}}
    if {flag!r} == 'symlink':
        attributes['st_mode'] = stat.S_IFLNK
    else:
        attributes['st_file_attributes'] = 0x400
    with patch.object(type(t.path),'lstat',return_value=SimpleNamespace(**attributes)), \
         patch.object(sqlite3,'connect',side_effect=AssertionError('unexpected connection')) as connect:
        try:
            v.validate_sqlite_target_identity(t)
        except v.MigrationValidationError:
            pass
        else:
            raise AssertionError('link accepted')
        connect.assert_not_called()
""")


def test_duplicate_data_is_detected_independently_of_schema_gate():
    child(BOOT + """
with v.create_temporary_sqlite_target() as t:
    with v.SQLiteBoundary(t):
        app = v.create_minimal_migration_app(t)
        v.run_alembic_upgrade(t,app)
        with sqlite3.connect(str(t.path)) as c:
""" + textwrap.indent(SEED, "            ") + """
            c.executescript("ALTER TABLE records RENAME TO old_records;"
                "CREATE TABLE records (id INTEGER NOT NULL PRIMARY KEY,datetime DATE NOT NULL,"
                "habit_id INTEGER NOT NULL,note VARCHAR(1024));"
                "INSERT INTO records VALUES (1,'2025-01-01',1,NULL),(2,'2025-01-01',1,NULL)")
            try:
                v.validate_initial_data_integrity(c)
            except v.MigrationValidationError as exc:
                assert str(exc) == 'duplicate_initial_key'
            else:
                raise AssertionError('duplicate data accepted')
""")


@pytest.mark.parametrize(("filesystem", "allowed"), [
    ("ext4", True), ("tmpfs", True), ("overlay", True), ("nfs", False), ("cifs", False),
    ("fuse.sshfs", False), ("unknown", False),
])
def test_linux_mount_policy_rejects_network_and_unknown_filesystems(filesystem, allowed):
    from tools.migration_validation import _linux_mount_is_local
    mounts = (
        "1 0 8:1 / / rw - ext4 /dev/root rw\n"
        f"2 1 0:2 / /tmp/remote rw - {filesystem} source rw\n"
    )
    assert _linux_mount_is_local("/tmp/remote/owned", mounts) is allowed


RESOURCE_BOOT = BOOT + """
from pathlib import Path
from types import SimpleNamespace
v.tempfile.gettempdir()
created = []
real_temporary_directory = v.tempfile.TemporaryDirectory
def track_directory(*args, **kwargs):
    temporary = real_temporary_directory(*args, **kwargs)
    created.append(Path(temporary.name))
    return temporary
"""


@pytest.mark.parametrize("failure", ["unsupported_platform", "unallowed_mount", "mountinfo_read"])
def test_pre_file_platform_failures_remove_only_original_empty_directory(failure):
    child(RESOURCE_BOOT + f"""
original_error = OSError('synthetic_mountinfo_failure')
with patch.object(v.tempfile, 'TemporaryDirectory', track_directory), \
     patch.object(v, 'os', SimpleNamespace(name='posix')), \
     patch.object(v.sys, 'platform', 'unsupported' if {failure!r} == 'unsupported_platform' else 'linux'), \
     patch.object(v.Path, 'read_text', side_effect=original_error if {failure!r} == 'mountinfo_read' else None,
                  return_value='synthetic mountinfo'), \
     patch.object(v, '_linux_mount_is_local', return_value=False):
    try:
        with v.create_temporary_sqlite_target():
            raise AssertionError('creation should fail')
    except Exception as exc:
        if {failure!r} == 'mountinfo_read':
            assert exc is original_error
        else:
            assert isinstance(exc, v.MigrationValidationError)
            assert str(exc) == (
                'unsupported_target_platform' if {failure!r} == 'unsupported_platform'
                else 'target_requires_local_mount'
            )
    else:
        raise AssertionError('expected platform refusal')
assert len(created) == 1 and not created[0].exists()
assert not v._OWNED
""")


def test_unverified_directory_is_retained_without_masking_identity_failure():
    child(RESOURCE_BOOT + """
original_error = v.MigrationValidationError('synthetic_directory_identity_failure')
with patch.object(v.tempfile, 'TemporaryDirectory', track_directory), \
     patch.object(v, '_identity', side_effect=original_error):
    try:
        with v.create_temporary_sqlite_target():
            raise AssertionError('creation should fail')
    except v.MigrationValidationError as exc:
        assert exc is original_error
        assert exc.__notes__ == ['temporary_resource_cleanup_failed']
assert created[0].is_dir() and not list(created[0].iterdir())
created[0].rmdir()
assert not v._OWNED
""")


def test_os_open_failure_removes_verified_empty_directory():
    child(RESOURCE_BOOT + """
original_error = OSError('synthetic_open_failure')
with patch.object(v.tempfile, 'TemporaryDirectory', track_directory), \
     patch.object(v.os, 'open', side_effect=original_error):
    try:
        with v.create_temporary_sqlite_target():
            raise AssertionError('creation should fail')
    except OSError as exc:
        assert exc is original_error
assert len(created) == 1 and not created[0].exists()
assert not v._OWNED
""")


@pytest.mark.parametrize("failure", ["target_construction", "ownership_registration"])
def test_post_open_pre_registration_failure_cleans_matching_file(failure):
    child(RESOURCE_BOOT + f"""
original_error = RuntimeError('synthetic_registration_failure')
real_target = v.SQLiteTarget
class RejectRegistration(dict):
    def __setitem__(self, key, value):
        raise original_error
with patch.object(v.tempfile, 'TemporaryDirectory', track_directory), \
     patch.object(v, 'SQLiteTarget', side_effect=original_error if {failure!r} == 'target_construction'
                  else real_target), \
     patch.object(v, '_OWNED', RejectRegistration()):
    try:
        with v.create_temporary_sqlite_target():
            raise AssertionError('creation should fail')
    except RuntimeError as exc:
        assert exc is original_error
    else:
        raise AssertionError('expected failure')
    assert not v._OWNED
assert len(created) == 1 and not created[0].exists()
""")


def test_replaced_directory_is_not_deleted_by_early_failure_or_finalizer():
    child(RESOURCE_BOOT + """
import gc
original_error = OSError('synthetic_creation_failure')
moved = []
def replace_directory(filename, *args):
    directory = Path(filename).parent
    saved = directory.with_name(directory.name + '-original')
    directory.rename(saved)
    directory.mkdir()
    (directory / 'unowned.txt').write_text('replacement must survive')
    moved.append(saved)
    raise original_error
with patch.object(v.tempfile, 'TemporaryDirectory', track_directory), \
     patch.object(v.os, 'open', replace_directory):
    try:
        with v.create_temporary_sqlite_target():
            raise AssertionError('creation should fail')
    except OSError as exc:
        assert exc is original_error
        assert exc.__notes__ == ['temporary_resource_cleanup_failed']
gc.collect()
assert (created[0] / 'unowned.txt').read_text() == 'replacement must survive'
assert moved[0].is_dir() and not list(moved[0].iterdir())
(created[0] / 'unowned.txt').unlink()
created[0].rmdir()
moved[0].rmdir()
assert not v._OWNED
""")


@pytest.mark.parametrize("window", ["before_path_validation", "before_registration"])
def test_replaced_created_file_is_never_authorized_or_deleted(window):
    child(RESOURCE_BOOT + f"""
import gc
original_error = RuntimeError('synthetic_registration_failure')
real_close = v.os.close
def replace_file():
    path = created[0] / 'dailydot_migration.sqlite3'
    path.rename(path.with_name('original.sqlite3'))
    path.write_bytes(b'replacement must survive')
def replacing_close(descriptor):
    real_close(descriptor)
    replace_file()
def replacing_target(path):
    replace_file()
    raise original_error
with patch.object(v.tempfile, 'TemporaryDirectory', track_directory), \
     patch.object(v.os, 'close', replacing_close if {window!r} == 'before_path_validation' else real_close), \
     patch.object(v, 'SQLiteTarget', replacing_target if {window!r} == 'before_registration' else v.SQLiteTarget):
    try:
        with v.create_temporary_sqlite_target():
            raise AssertionError('replacement accepted')
    except Exception as exc:
        if {window!r} == 'before_registration':
            assert exc is original_error
        else:
            assert isinstance(exc, v.MigrationValidationError) and str(exc) == 'target_file_changed'
        assert exc.__notes__ == ['temporary_resource_cleanup_failed']
gc.collect()
assert not v._OWNED
assert (created[0] / 'dailydot_migration.sqlite3').read_bytes() == b'replacement must survive'
assert (created[0] / 'original.sqlite3').exists()
(created[0] / 'dailydot_migration.sqlite3').unlink()
(created[0] / 'original.sqlite3').unlink()
created[0].rmdir()
""")


def test_missing_descriptor_identity_preserves_file_and_original_error():
    child(RESOURCE_BOOT + """
original_error = OSError('synthetic_fstat_failure')
with patch.object(v.tempfile, 'TemporaryDirectory', track_directory), \
     patch.object(v.os, 'fstat', side_effect=original_error):
    try:
        with v.create_temporary_sqlite_target():
            raise AssertionError('creation should fail')
    except OSError as exc:
        assert exc is original_error
        assert exc.__notes__ == ['temporary_resource_cleanup_failed']
path = created[0] / 'dailydot_migration.sqlite3'
assert path.exists()
path.unlink()
created[0].rmdir()
assert not v._OWNED
""")


def test_cleanup_failure_does_not_replace_original_creation_exception():
    child(RESOURCE_BOOT + """
original_error = OSError('synthetic_open_failure')
cleanup_error = OSError('synthetic_cleanup_failure')
with patch.object(v.tempfile, 'TemporaryDirectory', track_directory), \
     patch.object(v.os, 'open', side_effect=original_error), \
     patch.object(v.Path, 'rmdir', side_effect=cleanup_error):
    try:
        with v.create_temporary_sqlite_target():
            raise AssertionError('creation should fail')
    except OSError as exc:
        assert exc is original_error
        assert exc.__notes__ == ['temporary_resource_cleanup_failed']
assert created[0].is_dir() and not list(created[0].iterdir())
created[0].rmdir()
assert not v._OWNED
""")


def test_registered_target_cleanup_failure_preserves_body_exception():
    child(RESOURCE_BOOT + """
original_error = RuntimeError('synthetic_validation_failure')
with patch.object(v.tempfile, 'TemporaryDirectory', track_directory):
    try:
        with patch.object(v.Path, 'unlink', side_effect=OSError('synthetic_cleanup_failure')):
            with v.create_temporary_sqlite_target():
                raise original_error
    except RuntimeError as exc:
        assert exc is original_error
        assert exc.__notes__ == ['temporary_resource_cleanup_failed']
assert not v._OWNED
path = created[0] / 'dailydot_migration.sqlite3'
assert path.exists()
path.unlink()
created[0].rmdir()
""")


def test_real_alembic_flow_removes_both_database_and_directory():
    child(RESOURCE_BOOT + """
with patch.object(v.tempfile, 'TemporaryDirectory', track_directory):
    v.verify_migrations()
assert len(created) == 1 and not created[0].exists()
assert not v._OWNED
""")


def test_handled_caller_exception_does_not_hide_normal_cleanup_failure():
    child(RESOURCE_BOOT + """
handled = RuntimeError('already_handled_caller_error')
with patch.object(v.tempfile, 'TemporaryDirectory', track_directory):
    try:
        raise handled
    except RuntimeError:
        try:
            with patch.object(v.Path, 'rmdir', side_effect=OSError('synthetic_cleanup_failure')):
                with v.create_temporary_sqlite_target():
                    pass
        except v.MigrationValidationError as exc:
            assert str(exc) == 'temporary_resource_cleanup_failed'
        else:
            raise AssertionError('cleanup failure was suppressed')
assert not hasattr(handled, '__notes__')
assert not v._OWNED
assert created[0].is_dir() and not list(created[0].iterdir())
created[0].rmdir()
""")


def test_handled_caller_exception_does_not_hide_descriptor_close_failure():
    child(RESOURCE_BOOT + """
handled = RuntimeError('already_handled_caller_error')
real_close = v.os.close
def failed_close(descriptor):
    real_close(descriptor)
    raise OSError('synthetic_close_failure')
with patch.object(v.tempfile, 'TemporaryDirectory', track_directory):
    try:
        raise handled
    except RuntimeError:
        try:
            with patch.object(v.os, 'close', failed_close):
                with v.create_temporary_sqlite_target():
                    raise AssertionError('close failure was suppressed')
        except v.MigrationValidationError as exc:
            assert str(exc) == 'temporary_file_close_failed'
assert not hasattr(handled, '__notes__')
assert not created[0].exists() and not v._OWNED
""")
