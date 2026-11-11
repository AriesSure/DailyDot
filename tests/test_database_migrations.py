"""Real Alembic legacy upgrades on owned temporary files; no ORM schema creation."""

from tests.test_migration_validation import BOOT, SEED, child
import pytest


FLOW = BOOT + """
import sqlalchemy as sa
from app.extensions import db
from app.models import User, Habit, Record, Todo, Card
from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.migration import MigrationContext
from alembic.operations import Operations

def config():
    cfg = Config(str(v.ROOT / 'migrations' / 'alembic.ini'))
    cfg.set_main_option('script_location',str(v.ROOT / 'migrations'))
    return cfg

def revision():
    return ScriptDirectory.from_config(config()).get_revision(v.HEAD_REVISION).module

def seed(c):
    c.executescript(
        "INSERT INTO users VALUES(1,'one','one@example.invalid',NULL),(2,'two','two@example.invalid',NULL);"
        "INSERT INTO habits VALUES(11,'read','fas fa-book',NULL,NULL,'habit note',1),"
        "(12,'run','fas fa-star',NULL,NULL,NULL,2);"
        "INSERT INTO records VALUES(21,'2024-12-31',11,'历史 < & note'),"
        "(22,'2025-01-01',11,NULL),(23,'2025-01-01',12,'second user');"
        "INSERT INTO todos VALUES(31,'book','31/12/2024','09:00','todo note','2024-12-30 09:00:00',1),"
        "(32,'run','01/01/2025','10:00',NULL,'2024-12-31 10:00:00',2);")
    c.commit()
"""


def test_legacy_upgrade_preserves_all_values_and_matches_orm():
    child(FLOW + """
with v.create_temporary_sqlite_target() as t:
    with v.SQLiteBoundary(t):
        app=v.create_minimal_migration_app(t)
        build_initial(t,app)
        with sqlite3.connect(str(t.path)) as c:
            seed(c)
            before={table:c.execute('SELECT * FROM '+table+' ORDER BY id').fetchall()
                    for table in ('users','habits','records','todos')}
            try:
                c.execute("INSERT INTO todos VALUES(33,'book','01/01/2025','11:00',NULL,'2025-01-01 11:00:00',2)")
            except sqlite3.IntegrityError:
                c.rollback()
            else:
                raise AssertionError('old global uniqueness not enforced')
        v.run_alembic_upgrade(t,app)
        assert v.admit_migration_target(t)=='HEAD'
        with sqlite3.connect(str(t.path)) as c:
            for table in ('users','habits'):
                assert c.execute('SELECT * FROM '+table+' ORDER BY id').fetchall()==before[table]
            assert c.execute('SELECT id,checkin_date,habit_id,note FROM records ORDER BY id').fetchall()==before['records']
            assert c.execute('SELECT id,event,date,time,note,created_at,user_id FROM todos ORDER BY id').fetchall()==before['todos']
            assert c.execute('SELECT checkin_time FROM records').fetchall()==[(None,)]*3
            assert c.execute('SELECT completed FROM todos').fetchall()==[(0,),(0,)]
        with app.app_context():
            inspector=sa.inspect(db.engine)
            assert set(inspector.get_table_names())==set(db.metadata.tables)|{'alembic_version'}
            for table in db.metadata.tables.values():
                columns={col['name']:col for col in inspector.get_columns(table.name)}
                assert set(columns)==set(table.columns.keys())
                for expected in table.columns:
                    actual=columns[expected.name]
                    assert actual['type']._type_affinity is expected.type._type_affinity
                    assert getattr(actual['type'],'length',None)==getattr(expected.type,'length',None)
                    assert actual['nullable']==expected.nullable
                    assert actual['default'] is None and expected.server_default is None
                assert inspector.get_pk_constraint(table.name)['constrained_columns']==['id']
            assert db.session.get(Record,21).checkin_time is None
            assert not db.session.get(Todo,31).completed
            db.session.remove()
""")


def test_migrated_orm_crud_defaults_constraints_commit_and_rollback():
    child(FLOW + """
from datetime import date, timedelta
with v.create_temporary_sqlite_target() as t:
    with v.SQLiteBoundary(t):
        app=v.create_minimal_migration_app(t)
        build_initial(t,app)
        with sqlite3.connect(str(t.path)) as c:
            seed(c)
        v.run_alembic_upgrade(t,app)
        with app.app_context():
            old=db.session.get(Record,21)
            assert old.checkin_time is None
            old.note='updated historical note'
            db.session.commit()
            assert old.checkin_time is None
            record=Record(habit_id=11,checkin_date=date(2025,1,2),note='new')
            todo=Todo(user_id=2,event='book',date='02/01/2025',time='12:00')
            card=Card(user_id=1,date=date(2025,1,2),quote='first')
            db.session.add_all([record,todo,card])
            db.session.commit()
            assert record.checkin_time is not None and todo.completed is False
            todo.completed=True
            card.quote='updated'
            db.session.commit()
            assert db.session.get(Card,card.id).quote=='updated'
            assert db.session.get(Todo,todo.id).completed is True
            db.session.add(Card(user_id=1,date=date(2025,1,3)))
            db.session.flush()
            db.session.rollback()
            assert db.session.query(Card).count()==1
            for duplicate in (
                Record(habit_id=11,checkin_date=date(2025,1,2)),
                Card(user_id=1,date=date(2025,1,2)),
                Card(user_id=999,date=date(2025,1,4)),
            ):
                db.session.add(duplicate)
                try:
                    db.session.commit()
                except sa.exc.IntegrityError:
                    db.session.rollback()
                else:
                    raise AssertionError('constraint lost')
            db.session.add_all([
                Record(habit_id=11,checkin_date=date(2025,1,3)),
                Card(user_id=2,date=date(2025,1,2)),
                Todo(user_id=2,event='book',date='03/01/2025',time='12:00'),
            ])
            db.session.commit()
            db.session.delete(db.session.get(Card,card.id))
            db.session.commit()
            assert db.session.query(Card).count()==1
            db.session.remove()
""")


def test_unknown_time_template_json_statistics_and_ai_context():
    child(FLOW + """
import json
from jinja2 import ChoiceLoader, DictLoader, FileSystemLoader
from flask import render_template
from app.services.record_service import get_records_for_habit, check_in
from app.services.statistics_service import get_habit_stats
from app.ai.service import generate_report_data
from app.ai.prompt_templates import report_prompt
with v.create_temporary_sqlite_target() as t:
    with v.SQLiteBoundary(t):
        app=v.create_minimal_migration_app(t)
        build_initial(t,app)
        with sqlite3.connect(str(t.path)) as c:
            seed(c)
        v.run_alembic_upgrade(t,app)
        app.jinja_loader=ChoiceLoader([
            DictLoader({'base.html':'{% block content %}{% endblock %}'}),
            FileSystemLoader(str(v.ROOT/'app/templates')),
        ])
        app.jinja_env.globals['url_for']=lambda *args,**kwargs:'/test'
        with app.test_request_context('/'):
            records=get_records_for_habit(11)
            assert all(row.checkin_time is None for row in records)
            html=render_template('checkin_logs.html',habit=db.session.get(Habit,11),records=records)
            assert '时间未知' in html
            assert json.loads(app.json.dumps({'checkin_time':records[0].checkin_time}))=={'checkin_time':None}
            assert get_habit_stats(1)[0]['total_checkins']==2
            stats=generate_report_data(1,'weekly')
            prompt=report_prompt(stats)
            assert all('00:00' not in msg['content'] and 'checkin_time' not in msg['content'] for msg in prompt)
            created=check_in(11,1)
            assert created.checkin_time is not None
            db.session.remove()
""")


def test_downgrade_refuses_before_ddl_and_preserves_cards_and_data():
    child(FLOW + """
with v.create_temporary_sqlite_target() as t:
    with v.SQLiteBoundary(t):
        app=v.create_minimal_migration_app(t)
        build_initial(t,app)
        with sqlite3.connect(str(t.path)) as c:
            seed(c)
        v.run_alembic_upgrade(t,app)
        with app.app_context():
            db.session.add(Card(user_id=1,quote='must survive'))
            db.session.commit()
            db.session.remove()
        with sqlite3.connect(str(t.path)) as c:
            before=tuple(c.iterdump())
        migration=revision()
        with patch.object(migration.op,'get_bind',side_effect=AssertionError('downgrade touched database')) as bind:
            try:
                migration.downgrade()
            except RuntimeError as exc:
                assert str(exc)=='forward_migration_downgrade_requires_backup_restore'
            else:
                raise AssertionError('unsafe downgrade accepted')
            bind.assert_not_called()
        with app.app_context():
            try:
                command.downgrade(config(),v.REVISION)
            except RuntimeError as exc:
                assert str(exc)=='forward_migration_downgrade_requires_backup_restore'
            else:
                raise AssertionError('unsafe downgrade command accepted')
        with sqlite3.connect(str(t.path)) as c:
            assert tuple(c.iterdump())==before
""")


@pytest.mark.parametrize("mutation", [
    "ALTER TABLE todos ADD COLUMN completed BOOLEAN",
    "ALTER TABLE records ADD COLUMN checkin_time TIME",
    "ALTER TABLE records ADD COLUMN checkin_date DATE",
    "CREATE TABLE cards(id INTEGER)",
    "UPDATE alembic_version SET version_num='unknown'",
    "PRAGMA foreign_keys=OFF; UPDATE records SET habit_id=999 WHERE id=21",
    "CREATE VIEW unknown_view AS SELECT id FROM users",
    "CREATE INDEX unknown_index ON habits(icon)",
])
def test_revision_preflight_itself_refuses_before_any_ddl(mutation):
    child(FLOW + f"""
with v.create_temporary_sqlite_target() as t:
    with v.SQLiteBoundary(t):
        app=v.create_minimal_migration_app(t)
        build_initial(t,app)
        with sqlite3.connect(str(t.path)) as c:
            seed(c)
            c.executescript({mutation!r})
            c.commit()
            before=tuple(c.iterdump())
        with app.app_context(), db.engine.connect() as connection:
            statements=[]
            sa.event.listen(connection,'before_cursor_execute',
                            lambda conn,cursor,statement,parameters,context,executemany:statements.append(statement))
            with Operations.context(MigrationContext.configure(connection)):
                try:
                    revision().upgrade()
                except RuntimeError as exc:
                    assert str(exc)=='forward_migration_preflight_failed'
                else:
                    raise AssertionError('mixed schema accepted')
            assert not any(s.lstrip().upper().startswith(('CREATE ','DROP ','ALTER ','INSERT ','UPDATE ','DELETE '))
                           for s in statements)
        with sqlite3.connect(str(t.path)) as c:
            assert tuple(c.iterdump())==before
""")


@pytest.mark.parametrize("mutation", [
    "ALTER TABLE records ADD COLUMN datetime DATE",
    "DROP INDEX ix_todos_event; CREATE UNIQUE INDEX ix_todos_event ON todos(event)",
    "ALTER TABLE cards ADD COLUMN extra TEXT",
    "DROP TABLE cards",
    "UPDATE alembic_version SET version_num='unknown'",
    "ALTER TABLE todos RENAME COLUMN completed TO previous_completed",
    "CREATE TRIGGER bad AFTER INSERT ON cards BEGIN SELECT 1; END",
])
def test_mixed_head_is_rejected_before_alembic(mutation):
    child(FLOW + f"""
with v.create_temporary_sqlite_target() as t:
    with v.SQLiteBoundary(t):
        app=v.create_minimal_migration_app(t)
        v.run_alembic_upgrade(t,app)
        with sqlite3.connect(str(t.path)) as c:
            c.executescript({mutation!r})
            c.commit()
            before=tuple(c.iterdump())
        with patch.object(command,'upgrade') as upgrade:
            try:
                v.run_alembic_upgrade(t,app)
            except v.MigrationValidationError:
                pass
            else:
                raise AssertionError('mixed head accepted')
            upgrade.assert_not_called()
        with sqlite3.connect(str(t.path)) as c:
            assert tuple(c.iterdump())==before
""")


def test_partial_ddl_failure_is_detected_and_never_automatically_resumed():
    child(FLOW + """
with v.create_temporary_sqlite_target() as t:
    with v.SQLiteBoundary(t):
        app=v.create_minimal_migration_app(t)
        build_initial(t,app)
        with sqlite3.connect(str(t.path)) as c:
            seed(c)
        # Fail after records ALTER batches and before the todos ADD COLUMN.
        def fail_add_column(*args, **kwargs):
            raise RuntimeError('synthetic_ddl_failure')
        with patch('alembic.operations.Operations.add_column',new=fail_add_column):
            try:
                v.run_alembic_upgrade(t,app)
            except RuntimeError:
                pass
            else:
                raise AssertionError('failure ignored')
        with patch.object(command,'upgrade') as upgrade:
            try:
                v.run_alembic_upgrade(t,app)
            except v.MigrationValidationError:
                pass
            else:
                raise AssertionError('partial schema resumed')
            upgrade.assert_not_called()
""")


@pytest.mark.parametrize("table,old,new", [
    ("todos", "completed BOOLEAN NOT NULL", "completed BOOLEAN"),
    ("records", "UNIQUE (checkin_date, habit_id)", "UNIQUE (checkin_date, note)"),
])
def test_head_constraint_mismatch_refuses_without_command(table, old, new):
    child(FLOW + f"""
with v.create_temporary_sqlite_target() as t:
    with v.SQLiteBoundary(t):
        app=v.create_minimal_migration_app(t)
        v.run_alembic_upgrade(t,app)
        with sqlite3.connect(str(t.path)) as c:
            sql=c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?",({table!r},)).fetchone()[0]
            assert {old!r} in sql
            indexes=c.execute("SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL",({table!r},)).fetchall()
            c.execute('ALTER TABLE '+{table!r}+' RENAME TO previous_table')
            c.execute(sql.replace({old!r},{new!r}))
            c.execute('DROP TABLE previous_table')
            for statement, in indexes:
                c.execute(statement)
            c.commit()
            before=tuple(c.iterdump())
        with patch.object(command,'upgrade') as upgrade:
            try:
                v.run_alembic_upgrade(t,app)
            except v.MigrationValidationError:
                pass
            else:
                raise AssertionError('head constraint mismatch accepted')
            upgrade.assert_not_called()
        with sqlite3.connect(str(t.path)) as c:
            assert tuple(c.iterdump())==before
""")


def test_mysql_upgrade_sql_compiles_offline_without_mysql_connection():
    child(FLOW + """
import io
output=io.StringIO()
migration=revision()
with v.create_temporary_sqlite_target() as t, v.SQLiteBoundary(t):
    with Operations.context(MigrationContext.configure(dialect_name='mysql',
            opts={'as_sql':True,'literal_binds':True,'output_buffer':output})):
        # Only DDL compilation is exercised, never MySQL preflight or execution.
        with patch.object(migration,'validate_initial_schema'):
            migration.upgrade()
sql=output.getvalue()
assert 'CREATE TABLE cards' in sql and 'checkin_time TIME' in sql
assert 'UNIQUE (checkin_date, habit_id)' in sql
assert 'CREATE INDEX ix_todos_event' in sql and 'UPDATE todos SET completed=false' in sql
""")
