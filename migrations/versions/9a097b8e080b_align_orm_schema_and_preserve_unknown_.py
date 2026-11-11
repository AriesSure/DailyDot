"""Align ORM schema while preserving unknown historical check-in times.

Revision ID: 9a097b8e080b
Revises: df8b049a3e7e
"""
from datetime import date, datetime
import re

from alembic import op
import sqlalchemy as sa


revision = "9a097b8e080b"
down_revision = "df8b049a3e7e"
branch_labels = None
depends_on = None

# Frozen initial signature. Never import live application models here.
_COLUMNS = {
    "users": [("id", "int", None, False), ("username", "str", 64, False),
              ("email", "str", 120, False), ("password_hash", "str", 256, True)],
    "habits": [("id", "int", None, False), ("habit_name", "str", 64, False),
               ("icon", "str", 32, False), ("frequency", "str", 64, True),
               ("time_period", "str", 64, True), ("note", "str", 1024, True),
               ("user_id", "int", None, False)],
    "todos": [("id", "int", None, False), ("event", "str", 64, False),
              ("date", "str", 16, False), ("time", "str", 16, False),
              ("note", "str", 1024, True), ("created_at", "datetime", None, False),
              ("user_id", "int", None, False)],
    "records": [("id", "int", None, False), ("datetime", "date", None, False),
                ("habit_id", "int", None, False), ("note", "str", 1024, True)],
    "alembic_version": [("version_num", "str", 32, False)],
}
_INDEXES = {
    "users": {"ix_users_email": (True, ("email",)), "ix_users_username": (True, ("username",))},
    "habits": {"ix_habits_habit_name": (False, ("habit_name",)), "ix_habits_user_id": (False, ("user_id",))},
    "todos": {"ix_todos_event": (True, ("event",)), "ix_todos_user_id": (False, ("user_id",))},
    "records": {"ix_records_habit_id": (False, ("habit_id",))},
    "alembic_version": {},
}
_FOREIGN = {"habits": ("user_id", "users"), "todos": ("user_id", "users"),
            "records": ("habit_id", "habits")}
_TYPES = {"int": sa.Integer, "str": sa.String, "date": sa.Date, "datetime": sa.DateTime}


def _require(condition):
    if not condition:
        raise RuntimeError("forward_migration_preflight_failed")


def _check_data(connection):
    for table, columns in _COLUMNS.items():
        for row in connection.execute(sa.text(f"SELECT * FROM {table}")).mappings():
            for name, kind, length, nullable in columns:
                value = row[name]
                if value is None:
                    _require(nullable)
                elif kind == "int":
                    _require(type(value) is int)
                elif kind == "str":
                    _require(isinstance(value, str) and len(value) <= length)
                elif kind == "date":
                    if isinstance(value, str):
                        _require(date.fromisoformat(value).isoformat() == value)
                    else:
                        _require(type(value) is date)
                elif kind == "datetime":
                    _require(isinstance(value, (str, datetime)))
                    if isinstance(value, str):
                        datetime.fromisoformat(value)
    for table, (child, parent) in _FOREIGN.items():
        _require(connection.execute(sa.text(
            f"SELECT 1 FROM {table} c WHERE NOT EXISTS "
            f"(SELECT 1 FROM {parent} p WHERE p.id=c.{child}) LIMIT 1"
        )).first() is None)
    for table, keys in (("records", "datetime,habit_id"), ("users", "username"),
                        ("users", "email"), ("todos", "event")):
        _require(connection.execute(sa.text(
            f"SELECT 1 FROM {table} GROUP BY {keys} HAVING count(*)>1 LIMIT 1"
        )).first() is None)


def _check_initial(connection):
    inspector = sa.inspect(connection)
    _require(connection.dialect.name in {"sqlite", "mysql"})
    _require(set(inspector.get_table_names()) == set(_COLUMNS))
    _require(not inspector.get_view_names())
    _require(connection.execute(sa.text("SELECT version_num FROM alembic_version")).all()
             == [("df8b049a3e7e",)])
    if connection.dialect.name == "sqlite":
        _require(connection.exec_driver_sql("PRAGMA integrity_check").all() == [("ok",)])
        _require(not connection.exec_driver_sql("PRAGMA foreign_key_check").all())
        _require(not connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='trigger'"
        ).all())
        _require(not connection.exec_driver_sql("SELECT name FROM sqlite_temp_master").all())
        objects = connection.exec_driver_sql("SELECT type,name FROM sqlite_master").all()
        allowed = set(_COLUMNS) | {n for indexes in _INDEXES.values() for n in indexes}
        allowed |= {"sqlite_autoindex_records_1", "sqlite_autoindex_alembic_version_1"}
        _require(all(kind in {"table", "index"} and name in allowed for kind, name in objects))
    else:
        _require(not connection.execute(sa.text(
            "SELECT TRIGGER_NAME FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=DATABASE()"
        )).all())

    frozen_metadata = sa.MetaData()
    for table, expected in _COLUMNS.items():
        constraints = [sa.PrimaryKeyConstraint(
            "version_num" if table == "alembic_version" else "id",
            name="alembic_version_pkc" if table == "alembic_version" else None)]
        if table in _FOREIGN:
            child, parent = _FOREIGN[table]
            constraints.append(sa.ForeignKeyConstraint([child], [f"{parent}.id"]))
        if table == "records":
            constraints.append(sa.UniqueConstraint("datetime", "habit_id", name="uix_date_habit"))
        sa.Table(table, frozen_metadata, *[
            sa.Column(name, _TYPES[kind](length) if kind == "str" else _TYPES[kind](), nullable=nullable)
            for name, kind, length, nullable in expected
        ], *constraints)

    for table, expected in _COLUMNS.items():
        if connection.dialect.name == "sqlite":
            for index_row in connection.exec_driver_sql(f'PRAGMA index_list("{table}")'):
                _require(not index_row[4])
                keys = connection.exec_driver_sql(f'PRAGMA index_xinfo("{index_row[1]}")').all()
                _require(all(row[1] >= 0 and row[3] == 0 and row[4] == "BINARY"
                             for row in keys if row[5]))
            actual_ddl = connection.execute(sa.text(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=:name"
            ), {"name": table}).scalar_one()
            expected_ddl = str(sa.schema.CreateTable(frozen_metadata.tables[table]).compile(dialect=connection.dialect))
            normalize = lambda sql: re.sub(r'\s+', '', sql).replace('"', '').lower()
            _require(normalize(actual_ddl) == normalize(expected_ddl))
        columns = inspector.get_columns(table)
        _require([c["name"] for c in columns] == [c[0] for c in expected])
        for actual, (name, kind, length, nullable) in zip(columns, expected):
            _require(actual["type"]._type_affinity is _TYPES[kind])
            _require(getattr(actual["type"], "length", None) == length)
            if connection.dialect.name == "mysql":
                expected_type = frozen_metadata.tables[table].c[name].type.compile(dialect=connection.dialect)
                actual_type = actual["type"].compile(dialect=connection.dialect)
                # INTEGER(11) is the legacy signed INTEGER display width, not
                # a smaller integer domain. Reject unsigned/narrower variants.
                allowed_types = {expected_type, expected_type + "(11)"} if kind == "int" else {expected_type}
                _require(actual_type in allowed_types)
            _require(actual["nullable"] == nullable and actual.get("default") is None)
            _require(not actual.get("computed") and not actual.get("identity"))
        pk = "version_num" if table == "alembic_version" else "id"
        _require(inspector.get_pk_constraint(table)["constrained_columns"] == [pk])
        _require(not inspector.get_check_constraints(table))
        foreign = inspector.get_foreign_keys(table)
        if table in _FOREIGN:
            child, parent = _FOREIGN[table]
            _require(len(foreign) == 1)
            fk = foreign[0]
            _require(fk["constrained_columns"] == [child] and fk["referred_table"] == parent
                     and fk["referred_columns"] == ["id"] and fk.get("referred_schema") in (None, inspector.default_schema_name))
            options = fk.get("options", {})
            _require(all(options.get(key) in (None, "NO ACTION", "RESTRICT") for key in ("ondelete", "onupdate")))
            _require(not options.get("deferrable") and options.get("initially") is None)
        else:
            _require(not foreign)
        indexes = {}
        for index in inspector.get_indexes(table):
            _require(not index.get("column_sorting"))
            _require(not any(index.get("dialect_options", {}).values()))
            indexes[index["name"]] = (bool(index["unique"]), tuple(index["column_names"]))
        expected_indexes = dict(_INDEXES[table])
        # MySQL reflects a UNIQUE constraint as an index as well.
        if table == "records" and connection.dialect.name == "mysql":
            expected_indexes["uix_date_habit"] = (True, ("datetime", "habit_id"))
        _require(indexes == expected_indexes)
        uniques = {u["name"]: tuple(u["column_names"]) for u in inspector.get_unique_constraints(table)}
        expected_uniques = {"uix_date_habit": ("datetime", "habit_id")} if table == "records" else {}
        if connection.dialect.name == "mysql":
            expected_uniques.update({name: cols for name, (unique, cols) in _INDEXES[table].items() if unique})
        _require(uniques == expected_uniques)
    _check_data(connection)


def validate_initial_schema(connection):
    """Read-only preflight, also used by the revision's rejection tests."""
    try:
        _check_initial(connection)
    except Exception:
        raise RuntimeError("forward_migration_preflight_failed") from None


def upgrade():
    validate_initial_schema(op.get_bind())  # Complete every check before any DDL.
    with op.batch_alter_table("records") as batch:
        batch.drop_constraint("uix_date_habit", type_="unique")
        batch.alter_column("datetime", new_column_name="checkin_date",
                           existing_type=sa.Date(), existing_nullable=False)
        batch.add_column(sa.Column("checkin_time", sa.Time(), nullable=True))
    # Reflect the renamed column before creating its new constraint. Alembic
    # 1.12 batch transfers still use the old key within the rename batch.
    with op.batch_alter_table("records") as batch:
        batch.create_unique_constraint("uix_date_habit", ["checkin_date", "habit_id"])

    op.add_column("todos", sa.Column("completed", sa.Boolean(), nullable=True))
    op.execute(sa.text("UPDATE todos SET completed=:value").bindparams(value=False))
    with op.batch_alter_table("todos") as batch:
        batch.alter_column("completed", existing_type=sa.Boolean(), nullable=False)
        batch.drop_index("ix_todos_event")
        batch.create_index("ix_todos_event", ["event"], unique=False)

    op.create_table(
        "cards",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("image_url", sa.String(255), nullable=True),
        sa.Column("quote", sa.String(255), nullable=True),
        sa.Column("weekday", sa.String(10), nullable=True),
        sa.Column("streak", sa.Integer(), nullable=False),
        sa.Column("habits_completed", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.UniqueConstraint("date", "user_id", name="uix_date_user"),
    )
    op.create_index("ix_cards_user_id", "cards", ["user_id"], unique=False)


def downgrade():
    raise RuntimeError("forward_migration_downgrade_requires_backup_restore")
