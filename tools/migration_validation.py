"""Command admission for owned SQLite files, independent of the application factory.

Only EMPTY and the pinned initial schema are admitted. This module is inert on
import. The process-local guards are intended for a dedicated verifier process,
not for concurrent application threads or hostile Python code in that process.
"""

from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import os
from pathlib import Path
import re
import socket
import sqlite3
import sqlite3.dbapi2
import stat
import sys
import tempfile
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
REVISION = "df8b049a3e7e"
INITIAL_HASH = "04a07055253abadbbc580b61052510c22344b635a0f8b8a26a1df02b08ae33f1"
_COLUMNS = {
    "users": [("id", "INTEGER", 1), ("username", "VARCHAR(64)", 1),
              ("email", "VARCHAR(120)", 1), ("password_hash", "VARCHAR(256)", 0)],
    "habits": [("id", "INTEGER", 1), ("habit_name", "VARCHAR(64)", 1),
               ("icon", "VARCHAR(32)", 1), ("frequency", "VARCHAR(64)", 0),
               ("time_period", "VARCHAR(64)", 0), ("note", "VARCHAR(1024)", 0), ("user_id", "INTEGER", 1)],
    "todos": [("id", "INTEGER", 1), ("event", "VARCHAR(64)", 1), ("date", "VARCHAR(16)", 1),
              ("time", "VARCHAR(16)", 1), ("note", "VARCHAR(1024)", 0),
              ("created_at", "DATETIME", 1), ("user_id", "INTEGER", 1)],
    "records": [("id", "INTEGER", 1), ("datetime", "DATE", 1),
                ("habit_id", "INTEGER", 1), ("note", "VARCHAR(1024)", 0)],
    "alembic_version": [("version_num", "VARCHAR(32)", 1)],
}
_INDEXES = {
    "users": {"ix_users_username": (1, ("username",)), "ix_users_email": (1, ("email",))},
    "habits": {"ix_habits_habit_name": (0, ("habit_name",)), "ix_habits_user_id": (0, ("user_id",))},
    "todos": {"ix_todos_event": (1, ("event",)), "ix_todos_user_id": (0, ("user_id",))},
    "records": {"ix_records_habit_id": (0, ("habit_id",))},
    "alembic_version": {},
}
_FOREIGN = {"habits": ("user_id", "users", "id"), "todos": ("user_id", "users", "id"),
            "records": ("habit_id", "habits", "id")}
_OWNED = {}


def _linux_mount_is_local(path, mountinfo):
    mounts = []
    for line in mountinfo.splitlines():
        fields = line.split()
        divider = fields.index("-")
        mount = fields[4]
        for encoded, decoded in (("\\040", " "), ("\\011", "\t"), ("\\012", "\n"), ("\\134", "\\")):
            mount = mount.replace(encoded, decoded)
        if str(path) == mount or str(path).startswith(mount.rstrip("/") + "/"):
            mounts.append((len(mount), fields[divider + 1]))
    supported = {"ext2", "ext3", "ext4", "xfs", "btrfs", "tmpfs", "ramfs", "overlay", "zfs"}
    return bool(mounts and max(mounts)[1] in supported)


class MigrationValidationError(RuntimeError):
    """A fixed error code without file paths, data, credentials or SQL."""


def _require(condition, code):
    if not condition:
        raise MigrationValidationError(code)


def _identity(path, directory=False):
    return _stat_identity(path.lstat(), directory=directory)


def _stat_identity(info, directory=False):
    _require(not stat.S_ISLNK(info.st_mode)
             and not (getattr(info, "st_file_attributes", 0) & 0x400), "unsafe_target_link")
    _require(stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode), "unsafe_target_kind")
    _require(info.st_ino != 0 and (directory or info.st_nlink == 1), "unsafe_target_identity")
    return info.st_dev, info.st_ino


@dataclass(eq=False, frozen=True)
class SQLiteTarget:
    path: Path


def validate_sqlite_target_identity(target):
    _require(target in _OWNED, "unowned_target")
    owner = _OWNED[target]
    path = owner["path"]
    _require(target.path == path and path.is_absolute() and path.resolve() == path, "target_path_changed")
    _require(not str(path).startswith(("\\\\", "//")) and ":" not in path.name, "unsafe_target_path")
    for directory, expected in owner["directories"]:
        _require(_identity(directory, directory=True) == expected, "target_directory_changed")
    _require(_identity(path) == owner["identity"], "target_file_changed")
    return path


@contextmanager
def create_temporary_sqlite_target():
    temporary = tempfile.TemporaryDirectory(prefix="dailydot-migration-")
    target = None
    directories = []
    directory = None
    path = None
    file_identity = None
    original_error = None
    try:
        # All removal below is explicit and identity-checked, including failures
        # before a target can be entered in the ownership registry.
        temporary._finalizer.detach()
        directory = Path(temporary.name).absolute()
        for parent in (directory, *directory.parents):
            directories.append((parent, _identity(parent, directory=True)))
        _require(directory.resolve() == directory and not str(directory).startswith(("\\\\", "//")),
                 "unsafe_target_directory")
        if os.name == "nt":
            import ctypes
            _require(ctypes.windll.kernel32.GetDriveTypeW(str(directory.anchor)) == 3,
                     "target_requires_local_drive")
        else:
            _require(sys.platform == "linux", "unsupported_target_platform")
            mountinfo = Path("/proc/self/mountinfo").read_text(encoding="utf-8")
            _require(_linux_mount_is_local(directory, mountinfo), "target_requires_local_mount")
        path = directory / "dailydot_migration.sqlite3"
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        file_error = None
        try:
            # The descriptor identifies the file actually created by O_EXCL.
            # A path lookup alone could authorize a replacement file.
            file_identity = _stat_identity(os.fstat(descriptor))
        except BaseException as error:
            file_error = error
            raise
        finally:
            try:
                os.close(descriptor)
            except OSError:
                if file_error is None:
                    raise MigrationValidationError("temporary_file_close_failed") from None
                file_error.add_note("temporary_file_close_failed")
        _require(_identity(path) == file_identity, "target_file_changed")
        target = SQLiteTarget(path)
        _OWNED[target] = {
            "temporary": temporary, "path": path, "identity": file_identity,
            "directories": directories, "admitted": False, "boundary": None,
            "app": None, "engine": None, "creator": None,
        }
        validate_sqlite_target_identity(target)
        yield target
    except BaseException as error:
        original_error = error
        raise
    finally:
        try:
            if target is not None and target in _OWNED:
                cleanup_temporary_target(target)
            else:
                _cleanup_incomplete_target(directory, directories, path, file_identity)
        except Exception:
            if original_error is None:
                raise MigrationValidationError("temporary_resource_cleanup_failed") from None
            original_error.add_note("temporary_resource_cleanup_failed")
        finally:
            temporary._finalizer.detach()


def _cleanup_incomplete_target(directory, directories, path, file_identity):
    _require(directory is not None and directory.resolve() == directory
             and [p for p, _ in directories] == [directory, *directory.parents],
             "incomplete_directory_identity")
    for parent, expected in directories:
        _require(_identity(parent, directory=True) == expected, "target_directory_changed")
    entries = list(directory.iterdir())
    if file_identity is not None:
        _require(entries == [path] and _identity(path) == file_identity, "target_file_changed")
        path.unlink()
    else:
        _require(not entries, "unknown_cleanup_object")
    directory.rmdir()


def cleanup_temporary_target(target):
    owner = _OWNED.get(target)
    _require(owner is not None, "unowned_target")
    try:
        if owner["app"] is not None:
            from app.extensions import db
            with owner["app"].app_context():
                db.session.remove()
        if owner["engine"] is not None:
            owner["engine"].dispose()
        path = validate_sqlite_target_identity(target)
        allowed = {path.name, path.name + "-journal", path.name + "-wal", path.name + "-shm"}
        entries = list(path.parent.iterdir())
        _require(all(p.name in allowed for p in entries), "unknown_cleanup_object")
        for entry in entries:
            _identity(entry)
        for entry in entries:
            entry.unlink()
        path.parent.rmdir()
    finally:
        # Avoid TemporaryDirectory's implicit recursive fallback on an unsafe path.
        owner["temporary"]._finalizer.detach()
        _OWNED.pop(target, None)


def _authorize(action, arg1, arg2, database, source):
    if action in (sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH):
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() == "load_extension":
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_PRAGMA and (arg1 or "").lower() in {
        "writable_schema", "temp_store_directory", "data_store_directory",
    }:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


class SQLiteBoundary:
    """Process-local deny guards. Use only inside the standalone subprocess."""
    def __init__(self, target):
        self.target = target
        self.stack = ExitStack()
        self.attempts = []
        self.connections = []
        self.creating_app = False

    def deny(self, *args, **kwargs):
        self.attempts.append("forbidden_io")
        raise MigrationValidationError("forbidden_migration_io")

    def __enter__(self):
        validate_sqlite_target_identity(self.target)
        # This context is dedicated to the verifier process. Remove external
        # application settings before importing app.config indirectly.
        for name in ("DATABASE_URL", "TEST_DATABASE_URL", "SQLALCHEMY_DATABASE_URI",
                     "FLASK_APP", "FLASK_ENV", "SECRET_KEY"):
            if name in os.environ:
                del os.environ[name]
        owner = _OWNED[self.target]
        _require(owner["boundary"] is None, "boundary_already_active")
        import sqlalchemy as sa
        import requests
        import pymysql
        original_sqlite = sqlite3.dbapi2.connect
        original_engine_init = sa.engine.Engine.__init__
        original_connect = sa.engine.Engine.connect
        original_raw = sa.engine.Engine.raw_connection

        def connect(database, *args, **kwargs):
            expected = validate_sqlite_target_identity(self.target)
            _require(database == str(expected) and not args
                     and (not kwargs or (set(kwargs) == {"check_same_thread"}
                                         and kwargs["check_same_thread"] is False)),
                     "sqlite_connection_forbidden")
            connection = original_sqlite(database, **kwargs)
            self.connections.append(connection)
            validate_sqlite_target_identity(self.target)
            connection.execute("PRAGMA foreign_keys=ON")
            _require(connection.execute("PRAGMA foreign_keys").fetchone() == (1,), "sqlite_fk_disabled")
            connection.enable_load_extension(False)
            connection.set_authorizer(_authorize)
            return connection

        def engine_init(engine, pool, dialect, url, logging_name=None, echo=None,
                        query_cache_size=500, execution_options=None, hide_parameters=False):
            _require(self.creating_app and owner["admitted"] and owner["engine"] is None,
                     "engine_creation_not_authorized")
            _require(url.drivername == "sqlite" and not url.query and url.database == str(
                validate_sqlite_target_identity(self.target)), "engine_target_forbidden")
            original_engine_init(engine, pool, dialect, url, logging_name=logging_name, echo=echo,
                                 query_cache_size=query_cache_size, execution_options=execution_options,
                                 hide_parameters=hide_parameters)
            owner["engine"] = engine
            owner["creator"] = engine.pool._creator

        def check_engine(engine):
            _require(engine is owner["engine"], "engine_identity_changed")
            validate_sqlite_target_identity(self.target)
            _require(engine.url == sa.engine.URL.create("sqlite", database=str(self.target.path))
                     and engine.pool._creator is owner["creator"], "engine_target_changed")

        def engine_connect(engine, *args, **kwargs):
            check_engine(engine)
            return original_connect(engine, *args, **kwargs)

        def raw_connection(engine, *args, **kwargs):
            check_engine(engine)
            return original_raw(engine, *args, **kwargs)

        try:
            for name in ("connect", "connect_ex", "sendto", "send", "sendall"):
                self.stack.enter_context(patch.object(socket.socket, name, self.deny))
            for name in ("create_connection", "getaddrinfo", "gethostbyname", "gethostbyname_ex",
                         "gethostbyaddr", "getnameinfo"):
                self.stack.enter_context(patch.object(socket, name, self.deny))
            self.stack.enter_context(patch.object(requests.sessions.Session, "request", self.deny))
            self.stack.enter_context(patch.object(pymysql.connections.Connection, "connect", self.deny))
            self.stack.enter_context(patch.object(sqlite3, "connect", connect))
            self.stack.enter_context(patch.object(sqlite3.dbapi2, "connect", connect))
            self.stack.enter_context(patch.object(sqlite3, "Connection", self.deny))
            self.stack.enter_context(patch.object(sqlite3.dbapi2, "Connection", self.deny))
            self.stack.enter_context(patch.object(sa.engine.Engine, "__init__", engine_init))
            self.stack.enter_context(patch.object(sa.engine.Engine, "connect", engine_connect))
            self.stack.enter_context(patch.object(sa.engine.Engine, "raw_connection", raw_connection))
            owner["boundary"] = self
            return self
        except BaseException:
            self.stack.close()
            raise

    def __exit__(self, *exc):
        try:
            owner = _OWNED[self.target]
            if owner["engine"] is not None:
                owner["engine"].dispose()
            for connection in self.connections:
                connection.close()
        finally:
            _OWNED[self.target]["boundary"] = None
            self.stack.close()
        _require(not self.attempts, "forbidden_migration_io_recorded")


@contextmanager
def _connection(target):
    validate_sqlite_target_identity(target)
    _require(_OWNED[target]["boundary"] is not None, "boundary_required")
    connection = sqlite3.connect(str(target.path))
    try:
        connection.execute("PRAGMA query_only=ON")
        yield connection
    finally:
        connection.close()


def _canonical(sql):
    # Only compare fixed DDL signatures, never transform SQL to execute it.
    return re.sub(r'\s+', '', sql).replace('"', '').lower()


def inspect_database_state(connection):
    databases = connection.execute("PRAGMA database_list").fetchall()
    _require(len(databases) == 1 and databases[0][1] == "main", "unexpected_database")
    objects = connection.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
    ).fetchall()
    _require(not connection.execute("SELECT name FROM sqlite_temp_master").fetchall(), "temporary_objects")
    return objects


def validate_initial_schema_signature(connection, objects):
    tables = {name: sql for kind, name, table, sql in objects if kind == "table"}
    _require(set(tables) == set(_COLUMNS), "initial_tables_mismatch")
    expected_object_names = set(_COLUMNS)
    for table, expected in _COLUMNS.items():
        columns = connection.execute('PRAGMA table_xinfo("' + table + '")').fetchall()
        expected_pk = "version_num" if table == "alembic_version" else "id"
        actual = [(c[1], c[2].replace(" ", "").upper(), c[3]) for c in columns]
        _require(actual == expected and all(c[4] is None and c[6] == 0 for c in columns),
                 "initial_columns_mismatch")
        _require([c[1] for c in columns if c[5]] == [expected_pk]
                 and all(c[5] in (0, 1) for c in columns), "initial_pk_mismatch")
        definitions = [f"{name} {kind}" + (" NOT NULL" if nn else "") for name, kind, nn in expected]
        if table == "alembic_version":
            definitions.append("CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)")
        else:
            definitions.append("PRIMARY KEY (id)")
        if table in _FOREIGN:
            child, parent, key = _FOREIGN[table]
            definitions.append(f"FOREIGN KEY({child}) REFERENCES {parent} ({key})")
        if table == "records":
            definitions.append("CONSTRAINT uix_date_habit UNIQUE (datetime, habit_id)")
        expected_sql = f"CREATE TABLE {table} (" + ",".join(definitions) + ")"
        _require(_canonical(tables[table]) == _canonical(expected_sql), "initial_table_ddl_mismatch")
        foreign = connection.execute('PRAGMA foreign_key_list("' + table + '")').fetchall()
        target_fk = _FOREIGN.get(table)
        expected_fk = [] if target_fk is None else [
            (target_fk[1], target_fk[0], target_fk[2], "NO ACTION", "NO ACTION", "NONE")
        ]
        _require([(f[2], f[3], f[4], f[5], f[6], f[7]) for f in foreign] == expected_fk,
                 "initial_fk_mismatch")
        indexes = connection.execute('PRAGMA index_list("' + table + '")').fetchall()
        explicit = {}
        auto = []
        for _, name, unique, origin, partial in indexes:
            _require(not partial, "partial_index_forbidden")
            keys = [row for row in connection.execute('PRAGMA index_xinfo("' + name + '")') if row[5]]
            _require(all(row[1] >= 0 and row[3] == 0 and row[4] == "BINARY" for row in keys),
                     "index_definition_mismatch")
            signature = (unique, tuple(row[2] for row in keys))
            if origin == "c":
                explicit[name] = signature
            else:
                auto.append((origin, signature))
            expected_object_names.add(name)
        _require(explicit == _INDEXES[table], "initial_indexes_mismatch")
        expected_auto = {
            "records": [("u", (1, ("datetime", "habit_id")))],
            "alembic_version": [("pk", (1, ("version_num",)))],
        }.get(table, [])
        _require(auto == expected_auto, "initial_unique_mismatch")
    _require(all(kind in {"table", "index"} and name in expected_object_names
                 for kind, name, table, sql in objects), "unknown_schema_object")


def validate_initial_data_integrity(connection):
    _require(connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)], "sqlite_integrity_failed")
    _require(not connection.execute("PRAGMA foreign_key_check").fetchall(), "foreign_key_orphans")
    for table, columns in _COLUMNS.items():
        for row in connection.execute('SELECT * FROM "' + table + '"'):
            for value, (name, kind, nn) in zip(row, columns):
                if value is None:
                    _require(not nn, "null_initial_value")
                    continue
                if kind == "INTEGER":
                    _require(type(value) is int, "invalid_integer_value")
                else:
                    _require(isinstance(value, str), "invalid_text_value")
                    if kind.startswith("VARCHAR"):
                        _require(len(value) <= int(kind[8:-1]), "oversize_initial_value")
                    elif kind == "DATE":
                        try:
                            _require(date.fromisoformat(value).isoformat() == value, "invalid_initial_date")
                        except ValueError:
                            raise MigrationValidationError("invalid_initial_date") from None
                    elif kind == "DATETIME":
                        try:
                            datetime.fromisoformat(value)
                        except ValueError:
                            raise MigrationValidationError("invalid_initial_datetime") from None
    for table, keys in (("records", "datetime,habit_id"), ("todos", "event"),
                        ("users", "username"), ("users", "email")):
        duplicate = connection.execute(
            f"SELECT 1 FROM {table} GROUP BY {keys} HAVING count(*) > 1 LIMIT 1"
        ).fetchone()
        _require(duplicate is None, "duplicate_initial_key")


def _validate_revision_chain():
    versions = ROOT / "migrations" / "versions"
    files = sorted(versions.glob("*.py"))
    _require([p.name for p in files] == ["df8b049a3e7e_initial.py"], "revision_chain_not_approved")
    source = files[0].read_bytes().replace(b"\r\n", b"\n")
    _require(hashlib.sha256(source).hexdigest() == INITIAL_HASH, "initial_revision_changed")


def admit_migration_target(target):
    owner = _OWNED.get(target)
    _require(owner is not None, "unowned_target")
    owner["admitted"] = False
    _validate_revision_chain()
    with _connection(target) as connection:
        objects = inspect_database_state(connection)
        if not objects:
            state = "EMPTY"
        else:
            validate_initial_schema_signature(connection, objects)
            _require(connection.execute("SELECT version_num FROM alembic_version").fetchall()
                     == [(REVISION,)], "initial_version_mismatch")
            validate_initial_data_integrity(connection)
            state = "INITIAL"
    owner["admitted"] = True
    return state


def create_minimal_migration_app(target):
    _require(admit_migration_target(target) in {"EMPTY", "INITIAL"}, "admission_required")
    owner = _OWNED[target]
    _require(owner["app"] is None and owner["boundary"] is not None, "minimal_app_already_created")
    from flask import Flask
    from sqlalchemy.engine import URL
    from app.extensions import db, migrate
    # Importing app registers models but does not access the lazy default app.
    application = Flask("dailydot_migration_verifier", static_folder=None)
    application.config.update(
        TESTING=False, SQLALCHEMY_DATABASE_URI=URL.create("sqlite", database=str(target.path)),
        SQLALCHEMY_TRACK_MODIFICATIONS=False, SQLALCHEMY_ECHO=False,
    )
    owner["boundary"].creating_app = True
    try:
        db.init_app(application)
    finally:
        owner["boundary"].creating_app = False
    owner["app"] = application
    migrate.init_app(application, db)
    _require(set(db.metadata.tables) == {"users", "habits", "todos", "records", "cards"},
             "model_metadata_incomplete")
    return application


def run_alembic_upgrade(target, application):
    admit_migration_target(target)  # A fresh full gate on every invocation.
    owner = _OWNED[target]
    _require(application is owner["app"] and not application.testing, "migration_app_changed")
    from alembic import command
    from alembic.config import Config
    from app.extensions import db
    from sqlalchemy.engine import URL
    expected_url = URL.create("sqlite", database=str(validate_sqlite_target_identity(target)))
    _require(application.config["SQLALCHEMY_DATABASE_URI"] == expected_url
             and not application.config.get("SQLALCHEMY_BINDS")
             and not application.config.get("SQLALCHEMY_ENGINE_OPTIONS"), "migration_app_config_changed")
    with application.app_context():
        _require(set(db.engines) == {None} and db.engine is owner["engine"]
                 and db.engine.url == expected_url and db.engine.pool._creator is owner["creator"],
                 "migration_engine_changed")
        config = Config(str(ROOT / "migrations" / "alembic.ini"))
        config.set_main_option("script_location", str(ROOT / "migrations"))
        command.upgrade(config, "head")
    _require(admit_migration_target(target) == "INITIAL", "migration_result_mismatch")


def verify_migrations():
    with create_temporary_sqlite_target() as target:
        with SQLiteBoundary(target):
            _require(admit_migration_target(target) == "EMPTY", "fresh_database_required")
            application = create_minimal_migration_app(target)
            run_alembic_upgrade(target, application)
            run_alembic_upgrade(target, application)
