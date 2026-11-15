"""New revision tests using only the owned SQLite Alembic entry."""
import pytest
from tests.test_database_migrations import FLOW
from tests.test_migration_validation import child


def test_aligned_upgrade_preserves_every_old_table_and_real_chat_crud():
    child(FLOW + """
from app.models import Conversation, ChatTurn, ChatMessage
from alembic import command
with v.create_temporary_sqlite_target() as t, v.SQLiteBoundary(t):
    app=v.create_minimal_migration_app(t)
    build_initial(t,app)
    with sqlite3.connect(str(t.path)) as c:
        seed(c)
    with app.app_context():
        command.upgrade(config(),v.ALIGNED_REVISION)
    assert v.admit_migration_target(t)=='ALIGNED'
    with sqlite3.connect(str(t.path)) as c:
        c.execute("INSERT INTO cards(id,date,user_id,quote,streak,habits_completed) "
                  "VALUES(41,'2025-01-01',1,'synthetic preserved card',2,3)")
        c.commit()
        before={n:c.execute('SELECT * FROM '+n+' ORDER BY id').fetchall()
                for n in ('users','habits','records','todos','cards')}
    v.run_alembic_upgrade(t,app)
    with sqlite3.connect(str(t.path)) as c:
        for n,rows in before.items():
            assert c.execute('SELECT * FROM '+n+' ORDER BY id').fetchall()==rows
    with app.app_context():
        db.session.execute(sa.text('PRAGMA foreign_keys=ON'))
        assert db.session.execute(sa.text('PRAGMA foreign_keys')).scalar_one()==1
        convo=Conversation(user_id=1)
        db.session.add(convo);db.session.commit()
        turn=ChatTurn(conversation_id=convo.id,turn_no=1,client_request_id='a'*32,request_digest='b'*64)
        db.session.add(turn);db.session.commit()
        calls=[{'id':'one','type':'function','function':{'name':'list_user_habits','arguments':'{}'}}]
        msg=ChatMessage(turn_id=turn.id,position=0,role='assistant',tool_calls=calls)
        db.session.add(msg);db.session.commit()
        assert msg.tool_calls==calls and msg.created_at.utcoffset().total_seconds()==0
        db.session.remove()
    assert v.admit_migration_target(t)=='HEAD'
    with sqlite3.connect(str(t.path)) as c:
        snapshot=tuple(c.iterdump())
    v.run_alembic_upgrade(t,app)
    with sqlite3.connect(str(t.path)) as c:
        assert tuple(c.iterdump())==snapshot
    from alembic.script import ScriptDirectory
    migration=ScriptDirectory.from_config(config()).get_revision(v.CHAT_REVISION).module
    with patch.object(migration.op,'get_bind',side_effect=AssertionError('no DDL')) as bind:
        try: migration.downgrade()
        except RuntimeError as error:
            assert str(error)=='forward_migration_downgrade_requires_backup_restore'
        else: raise AssertionError('unsafe downgrade')
        bind.assert_not_called()
    with sqlite3.connect(str(t.path)) as c:
        assert tuple(c.iterdump())==snapshot
""")


@pytest.mark.parametrize('sql', [
    'DROP TABLE chat_messages',
    'ALTER TABLE chat_turns ADD COLUMN extra TEXT',
    'CREATE INDEX unexpected_chat ON chat_messages(content)',
    "CREATE VIEW unexpected_chat AS SELECT * FROM conversations",
])
def test_chat_mixed_head_rejected_before_alembic(sql):
    child(FLOW + f"""
with v.create_temporary_sqlite_target() as t, v.SQLiteBoundary(t):
    app=v.create_minimal_migration_app(t)
    v.run_alembic_upgrade(t,app)
    with sqlite3.connect(str(t.path)) as c:
        c.execute({sql!r});c.commit()
        before=tuple(c.iterdump())
    with patch.object(command,'upgrade') as upgrade:
        try: v.run_alembic_upgrade(t,app)
        except v.MigrationValidationError: pass
        else: raise AssertionError('mixed schema accepted')
        upgrade.assert_not_called()
    with sqlite3.connect(str(t.path)) as c:
        assert tuple(c.iterdump())==before
""")


def test_new_mysql_ddl_compiles_without_mysql_connection():
    child(FLOW + """
import io
output=io.StringIO()
migration=ScriptDirectory.from_config(config()).get_revision(v.CHAT_REVISION).module
with Operations.context(MigrationContext.configure(dialect_name='mysql',
    opts={'as_sql':True,'literal_binds':True,'output_buffer':output})):
    with patch.object(migration,'validate_previous_schema'):
        migration.upgrade()
sql=output.getvalue()
assert 'CREATE TABLE conversations' in sql and 'CREATE TABLE chat_turns' in sql
assert 'CREATE TABLE chat_messages' in sql and 'ON DELETE CASCADE' in sql
assert 'ck_chat_turns_status' in sql and 'ck_chat_messages_role' in sql
assert 'COLLATE utf8mb4_bin' in sql
assert sql.count('ENGINE=InnoDB') == 3
for table, statement in migration.DDL['mysql'].items():
    assert statement.rstrip().endswith('ENGINE=InnoDB'), table
""")


@pytest.mark.parametrize('sql', ['CREATE TABLE conversations(id INTEGER)', 'ALTER TABLE todos ADD COLUMN extra TEXT'])
def test_new_revision_preflight_rejects_before_any_ddl(sql):
    child(FLOW + f"""
with v.create_temporary_sqlite_target() as t, v.SQLiteBoundary(t):
    app=v.create_minimal_migration_app(t)
    with app.app_context():
        command.upgrade(config(),v.ALIGNED_REVISION)
    assert v.admit_migration_target(t)=='ALIGNED'
    with sqlite3.connect(str(t.path)) as c:
        c.execute({sql!r});c.commit()
        before=tuple(c.iterdump())
    migration=ScriptDirectory.from_config(config()).get_revision(v.CHAT_REVISION).module
    with app.app_context(), db.engine.connect() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            with patch.object(migration.op,'execute',wraps=migration.op.execute) as ddl:
                try: migration.upgrade()
                except RuntimeError as error:
                    assert str(error)=='conversation_migration_preflight_failed'
                else: raise AssertionError('partial old schema admitted')
                ddl.assert_not_called()
    with sqlite3.connect(str(t.path)) as c:
        assert tuple(c.iterdump())==before
""")


def test_manual_success_message_matches_pinned_head(monkeypatch, capsys):
    from unittest.mock import Mock
    from tools import verify_migrations as entry
    from tools.migration_validation import HEAD_REVISION
    verification = Mock()
    monkeypatch.setattr(entry, 'verify_migrations', verification)
    monkeypatch.setattr(entry.sys, 'argv', ['verify_migrations.py'])
    assert entry.main() == 0
    verification.assert_called_once_with()
    assert capsys.readouterr().out == (
        f'migration_validation_ok: empty -> {HEAD_REVISION}; repeated upgrade verified\n')


@pytest.mark.parametrize('count', [0, 2])
def test_mysql_parent_engine_preflight_rejects_offline(count):
    import importlib.util
    from unittest.mock import Mock
    from tools import migration_validation as validation
    path = validation.ROOT / 'migrations/versions' / validation.CHAT_FILE
    spec = importlib.util.spec_from_file_location('chat_revision_engine_test', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    connection = Mock()
    connection.execute.return_value.scalar_one.return_value = count
    with pytest.raises(RuntimeError, match='conversation_migration_preflight_failed'):
        migration.validate_innodb_parent(connection)
    sql = str(connection.execute.call_args.args[0])
    assert "TABLE_NAME='users'" in sql and "ENGINE='InnoDB'" in sql and 'TABLE_SCHEMA=DATABASE()' in sql
