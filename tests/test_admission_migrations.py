"""Gate revisions and real independent SQLite connections, using the owned verifier."""
import importlib.util
from unittest.mock import Mock, patch

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from tools import migration_validation as validation
from tests.test_database_migrations import FLOW
from tests.test_migration_validation import child


def test_gate_upgrade_from_chat_preserves_rows_seed_and_repeat():
    child(FLOW + """
from alembic import command
with v.create_temporary_sqlite_target() as target, v.SQLiteBoundary(target):
    app=v.create_minimal_migration_app(target)
    with app.app_context():
        command.upgrade(config(),v.REVISION)
    with sqlite3.connect(str(target.path)) as connection:
        seed(connection)
    with app.app_context():
        command.upgrade(config(),v.CHAT_REVISION)
    assert v.admit_migration_target(target)=='CHAT'
    with sqlite3.connect(str(target.path)) as connection:
        connection.execute("INSERT INTO cards(id,date,user_id,streak,habits_completed) VALUES(51,'2025-01-01',1,2,3)")
        connection.execute("INSERT INTO conversations VALUES(?,1,'旧会话',?,?,2,NULL,NULL,NULL)",
                           ('c'*32,'2025-01-01 00:00:00','2025-01-01 00:00:00'))
        connection.execute("INSERT INTO chat_turns VALUES(61,?,1,?,?,'completed',?,?,NULL)",
                           ('c'*32,'d'*32,'e'*64,'2025-01-01 00:00:00','2025-01-01 00:00:01'))
        for position,role,content in [(0,'user','合成问题'),(1,'assistant','合成回答')]:
            connection.execute("INSERT INTO chat_messages(turn_id,position,role,content,created_at) VALUES(61,?,?,?,?)",
                               (position,role,content,'2025-01-01 00:00:00'))
        connection.commit()
        names=('users','habits','records','todos','cards','conversations','chat_turns','chat_messages')
        before={n:connection.execute('SELECT * FROM '+n+' ORDER BY id').fetchall() for n in names}
    v.run_alembic_upgrade(target,app)
    with sqlite3.connect(str(target.path)) as connection:
        assert connection.execute('SELECT * FROM agent_admission_gate').fetchall()==[('global',0)]
        for name,rows in before.items():
            assert connection.execute('SELECT * FROM '+name+' ORDER BY id').fetchall()==rows
    v.run_alembic_upgrade(target,app)
    assert v.admit_migration_target(target)=='HEAD'
""")


def test_shared_real_connection_cases_on_file_sqlite():
    child(FLOW + """
from tests.agent_admission_cases import verify_concurrency
with v.create_temporary_sqlite_target() as target, v.SQLiteBoundary(target):
    app=v.create_minimal_migration_app(target)
    v.run_alembic_upgrade(target,app)
    with app.app_context():
        proof=verify_concurrency(app,db.engine)
        assert all(value is True for value in proof.values())
    assert v.admit_migration_target(target)=='HEAD'
""")


def test_gate_mysql_ddl_and_downgrade_are_offline_explicit():
    child(FLOW + """
import io
from unittest.mock import patch
migration=ScriptDirectory.from_config(config()).get_revision(v.HEAD_REVISION).module
output=io.StringIO()
with Operations.context(MigrationContext.configure(dialect_name='mysql',
        opts={'as_sql':True,'literal_binds':True,'output_buffer':output})):
    with patch.object(migration,'validate_previous_schema'):
        migration.upgrade()
sql=output.getvalue()
assert 'ENGINE=InnoDB' in sql and 'ck_agent_gate_key' in sql and 'ck_agent_gate_version' in sql
assert "VALUES ('global',0)" in sql
with patch.object(migration.op,'execute') as ddl:
    try: migration.downgrade()
    except RuntimeError as error: assert str(error)=='forward_migration_downgrade_requires_backup_restore'
    else: raise AssertionError('unsafe downgrade')
    ddl.assert_not_called()
""")


@pytest.mark.parametrize('mutation', [None, 'view', 'checks', 'cascade', 'index', 'auto'])
def test_mysql_preflight_reflection_logic_offline(mutation):
    """Reflection logic only, not a live MySQL compatibility result."""
    spec = importlib.util.spec_from_file_location(
        'gate_revision_offline', validation.ROOT / 'migrations/versions' / validation.GATE_FILE)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    inspector = Mock(default_schema_name='dailydot_test')
    inspector.get_table_names.return_value = list(migration.PREVIOUS)
    inspector.get_view_names.return_value = ['unknown'] if mutation == 'view' else []

    def columns(name):
        values = []
        for key, kind, nullable in migration.PREVIOUS[name]['columns']:
            if kind.startswith('VARCHAR'):
                dtype = sa.String(int(kind[8:-1]))
            else:
                dtype = {'INTEGER': sa.Integer, 'DATE': sa.Date, 'TIME': sa.Time, 'TEXT': sa.Text,
                         'BOOLEAN': sa.Boolean, 'DATETIME': sa.DateTime, 'JSON': sa.JSON}[kind]()
            auto = key == 'id' and name != 'conversations'
            if mutation == 'auto' and name == 'users' and key == 'id':
                auto = False
            values.append({'name': key, 'type': dtype, 'nullable': nullable,
                           'default': None, 'autoincrement': auto})
        return values

    def indexes(name):
        row = migration.PREVIOUS[name]
        values = [{'name': key, 'unique': unique, 'column_names': cols}
                  for key, unique, cols in row['indexes']]
        values += [{'name': key, 'unique': True, 'column_names': cols} for key, cols in row['uniques']]
        if mutation == 'index' and name == 'users':
            values.append({'name': 'unknown', 'unique': False, 'column_names': ['id']})
        return values

    def checks(name):
        values = [{'name': key, 'sqltext': '(' + sql + ')'} for key, sql in migration.PREVIOUS[name]['checks']]
        if mutation == 'checks' and name == 'chat_turns':
            values[0]['sqltext'] = "status IN ('pending','completed','failed','interrupted','extra')"
        return values

    def foreign(name):
        return [{'name': key or name + '_ibfk_1', 'constrained_columns': cols, 'referred_table': parent,
                 'referred_columns': target, 'options': {'ondelete': 'RESTRICT' if mutation == 'cascade'
                                                                     else opts.get('ondelete', 'RESTRICT')}}
                for key, cols, parent, target, opts in migration.PREVIOUS[name]['foreign']]

    inspector.get_columns.side_effect = columns
    inspector.get_pk_constraint.side_effect = lambda name: {'constrained_columns': migration.PREVIOUS[name]['pk']}
    inspector.get_indexes.side_effect = indexes
    inspector.get_unique_constraints.side_effect = lambda name: [
        {'name': i['name'], 'column_names': i['column_names']} for i in indexes(name) if i['unique']]
    inspector.get_check_constraints.side_effect = checks
    inspector.get_foreign_keys.side_effect = foreign
    connection = Mock(dialect=mysql.dialect())

    def execute(statement):
        sql = str(statement)
        if sql == 'SELECT VERSION()':
            return Mock(scalar_one=lambda: '8.0.46')
        if 'SCHEMATA' in sql:
            return Mock(scalar_one=lambda: 'utf8mb4')
        rows = [(migration.down_revision,)] if sql.startswith('SELECT version_num') else [
            ('InnoDB', 'utf8mb4') for _ in migration.PREVIOUS]
        return Mock(all=lambda: rows, scalar_one=lambda: 0)

    connection.execute.side_effect = execute
    with patch.object(migration.sa, 'inspect', return_value=inspector):
        if mutation:
            with pytest.raises(RuntimeError, match='agent_gate_migration_preflight_failed'):
                migration.validate_previous_schema(connection)
        else:
            migration.validate_previous_schema(connection)
