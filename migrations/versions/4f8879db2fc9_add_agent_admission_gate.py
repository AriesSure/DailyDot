"""Add the fixed global Agent admission gate; history is never rewritten."""
import re
import sqlalchemy as sa
from alembic import op

revision = '4f8879db2fc9'
down_revision = '716951abf77d'
branch_labels = None
depends_on = None

PREVIOUS = {
    'alembic_version': {
        'checks': [],
        'columns': [['version_num', 'VARCHAR(32)', False]],
        'foreign': [],
        'indexes': [],
        'pk': ['version_num'],
        'uniques': [],
    },
    'cards': {
        'checks': [],
        'columns': [
            ['id', 'INTEGER', False],
            ['date', 'DATE', False],
            ['user_id', 'INTEGER', False],
            ['image_url', 'VARCHAR(255)', True],
            ['quote', 'VARCHAR(255)', True],
            ['weekday', 'VARCHAR(10)', True],
            ['streak', 'INTEGER', False],
            ['habits_completed', 'INTEGER', False],
        ],
        'foreign': [[None, ['user_id'], 'users', ['id'], {}]],
        'indexes': [['ix_cards_user_id', 0, ['user_id']]],
        'pk': ['id'],
        'uniques': [['uix_date_user', ['date', 'user_id']]],
    },
    'chat_messages': {
        'checks': [
            ['ck_chat_messages_position', 'position >= 0'],
            [
                'ck_chat_messages_role',
                "role IN ('user','assistant','tool') AND length(role) = length(trim(role))",
            ],
        ],
        'columns': [
            ['id', 'INTEGER', False],
            ['turn_id', 'INTEGER', False],
            ['position', 'INTEGER', False],
            ['role', 'VARCHAR(16)', False],
            ['content', 'TEXT', True],
            ['tool_calls', 'JSON', True],
            ['tool_call_id', 'VARCHAR(100)', True],
            ['tool_name', 'VARCHAR(64)', True],
            ['created_at', 'DATETIME', False],
        ],
        'foreign': [['fk_chat_messages_turn', ['turn_id'], 'chat_turns', ['id'], {'ondelete': 'CASCADE'}]],
        'indexes': [['ix_chat_messages_turn', 0, ['turn_id']]],
        'pk': ['id'],
        'uniques': [['uq_chat_messages_position', ['turn_id', 'position']]],
    },
    'chat_turns': {
        'checks': [
            ['ck_chat_turns_number', 'turn_no >= 1'],
            [
                'ck_chat_turns_status',
                (
                    "status IN ('pending','completed','failed','interrupted') AND length(status)"
                    ' = length(trim(status))'
                ),
            ],
        ],
        'columns': [
            ['id', 'INTEGER', False],
            ['conversation_id', 'VARCHAR(32)', False],
            ['turn_no', 'INTEGER', False],
            ['client_request_id', 'VARCHAR(32)', False],
            ['request_digest', 'VARCHAR(64)', False],
            ['status', 'VARCHAR(16)', False],
            ['created_at', 'DATETIME', False],
            ['finished_at', 'DATETIME', True],
            ['error_code', 'VARCHAR(64)', True],
        ],
        'foreign': [
            [
                'fk_chat_turns_conversation',
                ['conversation_id'],
                'conversations',
                ['id'],
                {
                    'ondelete': 'CASCADE',
                },
            ],
        ],
        'indexes': [['ix_chat_turns_conversation', 0, ['conversation_id']]],
        'pk': ['id'],
        'uniques': [
            ['uq_chat_turns_number', ['conversation_id', 'turn_no']],
            ['uq_chat_turns_request', ['conversation_id', 'client_request_id']],
        ],
    },
    'conversations': {
        'checks': [['ck_conversations_next_turn', 'next_turn_no >= 1']],
        'columns': [
            ['id', 'VARCHAR(32)', False],
            ['user_id', 'INTEGER', False],
            ['title', 'VARCHAR(120)', False],
            ['created_at', 'DATETIME', False],
            ['updated_at', 'DATETIME', False],
            ['next_turn_no', 'INTEGER', False],
            ['active_request_id', 'VARCHAR(32)', True],
            ['active_started_at', 'DATETIME', True],
            ['archived_at', 'DATETIME', True],
        ],
        'foreign': [['fk_conversations_user', ['user_id'], 'users', ['id'], {'ondelete': 'RESTRICT'}]],
        'indexes': [['ix_conversations_owner_updated', 0, ['user_id', 'updated_at', 'id']]],
        'pk': ['id'],
        'uniques': [],
    },
    'habits': {
        'checks': [],
        'columns': [
            ['id', 'INTEGER', False],
            ['habit_name', 'VARCHAR(64)', False],
            ['icon', 'VARCHAR(32)', False],
            ['frequency', 'VARCHAR(64)', True],
            ['time_period', 'VARCHAR(64)', True],
            ['note', 'VARCHAR(1024)', True],
            ['user_id', 'INTEGER', False],
        ],
        'foreign': [[None, ['user_id'], 'users', ['id'], {}]],
        'indexes': [['ix_habits_habit_name', 0, ['habit_name']], ['ix_habits_user_id', 0, ['user_id']]],
        'pk': ['id'],
        'uniques': [],
    },
    'records': {
        'checks': [],
        'columns': [
            ['id', 'INTEGER', False],
            ['checkin_date', 'DATE', False],
            ['habit_id', 'INTEGER', False],
            ['note', 'VARCHAR(1024)', True],
            ['checkin_time', 'TIME', True],
        ],
        'foreign': [[None, ['habit_id'], 'habits', ['id'], {}]],
        'indexes': [['ix_records_habit_id', 0, ['habit_id']]],
        'pk': ['id'],
        'uniques': [['uix_date_habit', ['checkin_date', 'habit_id']]],
    },
    'todos': {
        'checks': [],
        'columns': [
            ['id', 'INTEGER', False],
            ['event', 'VARCHAR(64)', False],
            ['date', 'VARCHAR(16)', False],
            ['time', 'VARCHAR(16)', False],
            ['note', 'VARCHAR(1024)', True],
            ['created_at', 'DATETIME', False],
            ['user_id', 'INTEGER', False],
            ['completed', 'BOOLEAN', False],
        ],
        'foreign': [[None, ['user_id'], 'users', ['id'], {}]],
        'indexes': [['ix_todos_event', 0, ['event']], ['ix_todos_user_id', 0, ['user_id']]],
        'pk': ['id'],
        'uniques': [],
    },
    'users': {
        'checks': [],
        'columns': [
            ['id', 'INTEGER', False],
            ['username', 'VARCHAR(64)', False],
            ['email', 'VARCHAR(120)', False],
            ['password_hash', 'VARCHAR(256)', True],
        ],
        'foreign': [],
        'indexes': [['ix_users_email', 1, ['email']], ['ix_users_username', 1, ['username']]],
        'pk': ['id'],
        'uniques': [],
    },
}

SQLITE_PREVIOUS = {
    'alembic_version': (
        'CREATE TABLE alembic_version (\n\tversion_num VARCHAR(32) NOT NULL, \n\tCONSTRA'
        'INT alembic_version_pkc PRIMARY KEY (version_num)\n)'
    ),
    'cards': (
        'CREATE TABLE cards (\n\tid INTEGER NOT NULL, \n\tdate DATE NOT NULL, \n\tuser_id '
        'INTEGER NOT NULL, \n\timage_url VARCHAR(255), \n\tquote VARCHAR(255), \n\tweekday'
        ' VARCHAR(10), \n\tstreak INTEGER NOT NULL, \n\thabits_completed INTEGER NOT NUL'
        'L, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(user_id) REFERENCES users (id), \n\tCONS'
        'TRAINT uix_date_user UNIQUE (date, user_id)\n)'
    ),
    'chat_messages': (
        'CREATE TABLE chat_messages (\n\tid INTEGER NOT NULL, \n\tturn_id INTEGER NOT NU'
        'LL, \n\tposition INTEGER NOT NULL, \n\trole VARCHAR(16) COLLATE "BINARY" NOT NU'
        'LL, \n\tcontent TEXT, \n\ttool_calls JSON, \n\ttool_call_id VARCHAR(100), \n\ttool_'
        'name VARCHAR(64), \n\tcreated_at DATETIME NOT NULL, \n\tCONSTRAINT pk_chat_mess'
        'ages PRIMARY KEY (id), \n\tCONSTRAINT fk_chat_messages_turn FOREIGN KEY(turn_'
        'id) REFERENCES chat_turns (id) ON DELETE CASCADE, \n\tCONSTRAINT uq_chat_mess'
        'ages_position UNIQUE (turn_id, position), \n\tCONSTRAINT ck_chat_messages_rol'
        "e CHECK (role IN ('user','assistant','tool') AND length(role) = length(trim"
        '(role))), \n\tCONSTRAINT ck_chat_messages_position CHECK (position >= 0)\n)'
    ),
    'chat_turns': (
        'CREATE TABLE chat_turns (\n\tid INTEGER NOT NULL, \n\tconversation_id VARCHAR(3'
        '2) NOT NULL, \n\tturn_no INTEGER NOT NULL, \n\tclient_request_id VARCHAR(32) NO'
        'T NULL, \n\trequest_digest VARCHAR(64) NOT NULL, \n\tstatus VARCHAR(16) COLLATE'
        ' "BINARY" NOT NULL, \n\tcreated_at DATETIME NOT NULL, \n\tfinished_at DATETIME,'
        ' \n\terror_code VARCHAR(64), \n\tCONSTRAINT pk_chat_turns PRIMARY KEY (id), \n\tC'
        'ONSTRAINT fk_chat_turns_conversation FOREIGN KEY(conversation_id) REFERENCE'
        'S conversations (id) ON DELETE CASCADE, \n\tCONSTRAINT uq_chat_turns_number U'
        'NIQUE (conversation_id, turn_no), \n\tCONSTRAINT uq_chat_turns_request UNIQUE'
        ' (conversation_id, client_request_id), \n\tCONSTRAINT ck_chat_turns_status CH'
        "ECK (status IN ('pending','completed','failed','interrupted') AND length(st"
        'atus) = length(trim(status))), \n\tCONSTRAINT ck_chat_turns_number CHECK (tur'
        'n_no >= 1)\n)'
    ),
    'conversations': (
        'CREATE TABLE conversations (\n\tid VARCHAR(32) NOT NULL, \n\tuser_id INTEGER NO'
        'T NULL, \n\ttitle VARCHAR(120) NOT NULL, \n\tcreated_at DATETIME NOT NULL, \n\tup'
        'dated_at DATETIME NOT NULL, \n\tnext_turn_no INTEGER NOT NULL, \n\tactive_reque'
        'st_id VARCHAR(32), \n\tactive_started_at DATETIME, \n\tarchived_at DATETIME, \n\t'
        'CONSTRAINT pk_conversations PRIMARY KEY (id), \n\tCONSTRAINT fk_conversations'
        '_user FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE RESTRICT, \n\tCONS'
        'TRAINT ck_conversations_next_turn CHECK (next_turn_no >= 1)\n)'
    ),
    'habits': (
        'CREATE TABLE habits (\n\tid INTEGER NOT NULL, \n\thabit_name VARCHAR(64) NOT NU'
        'LL, \n\ticon VARCHAR(32) NOT NULL, \n\tfrequency VARCHAR(64), \n\ttime_period VAR'
        'CHAR(64), \n\tnote VARCHAR(1024), \n\tuser_id INTEGER NOT NULL, \n\tPRIMARY KEY ('
        'id), \n\tFOREIGN KEY(user_id) REFERENCES users (id)\n)'
    ),
    'records': (
        'CREATE TABLE "records" (\n\tid INTEGER NOT NULL, \n\tcheckin_date DATE NOT NULL'
        ', \n\thabit_id INTEGER NOT NULL, \n\tnote VARCHAR(1024), \n\tcheckin_time TIME, \n'
        '\tPRIMARY KEY (id), \n\tCONSTRAINT uix_date_habit UNIQUE (checkin_date, habit_'
        'id), \n\tFOREIGN KEY(habit_id) REFERENCES habits (id)\n)'
    ),
    'todos': (
        'CREATE TABLE "todos" (\n\tid INTEGER NOT NULL, \n\tevent VARCHAR(64) NOT NULL, '
        '\n\tdate VARCHAR(16) NOT NULL, \n\ttime VARCHAR(16) NOT NULL, \n\tnote VARCHAR(10'
        '24), \n\tcreated_at DATETIME NOT NULL, \n\tuser_id INTEGER NOT NULL, \n\tcomplete'
        'd BOOLEAN NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(user_id) REFERENCES u'
        'sers (id)\n)'
    ),
    'users': (
        'CREATE TABLE users (\n\tid INTEGER NOT NULL, \n\tusername VARCHAR(64) NOT NULL,'
        ' \n\temail VARCHAR(120) NOT NULL, \n\tpassword_hash VARCHAR(256), \n\tPRIMARY KEY'
        ' (id)\n)'
    ),
}

GATE_DDL = {
    'sqlite': "CREATE TABLE agent_admission_gate (gate_key VARCHAR(16) COLLATE BINARY NOT NULL, "
              "lock_version BIGINT NOT NULL, CONSTRAINT pk_agent_admission_gate PRIMARY KEY (gate_key), "
              "CONSTRAINT ck_agent_gate_key CHECK (gate_key = 'global' AND length(gate_key) = 6), "
              "CONSTRAINT ck_agent_gate_version CHECK (lock_version >= 0))",
    'mysql': "CREATE TABLE agent_admission_gate (gate_key VARCHAR(16) COLLATE utf8mb4_bin NOT NULL, "
             "lock_version BIGINT NOT NULL, CONSTRAINT pk_agent_admission_gate PRIMARY KEY (gate_key), "
             "CONSTRAINT ck_agent_gate_key CHECK (gate_key = 'global' AND length(gate_key) = 6), "
             "CONSTRAINT ck_agent_gate_version CHECK (lock_version >= 0)) ENGINE=InnoDB",
}


def require(value):
    if not value:
        raise RuntimeError('agent_gate_migration_preflight_failed')


def canonical(sql):
    parts = re.findall(r"'(?:''|[^'])*'|[^']+", sql)
    return ''.join(p if p.startswith("'") else re.sub(r'\s+|`|"|_utf8mb4$', '', p).lower() for p in parts)


def check_rule(sql):
    text = canonical(sql)
    while text.startswith('(') and text.endswith(')'):
        depth, quoted, outer = 0, False, True
        for i, char in enumerate(text):
            if char == "'":
                quoted = not quoted
            elif not quoted:
                depth += (char == '(') - (char == ')')
                if depth == 0 and i < len(text)-1:
                    outer = False
                    break
        if not outer:
            break
        text = text[1:-1]
    return text


def valid_check(actual, expected):
    expected = canonical(expected)
    allowed = {expected}
    if 'and' in expected:
        left, right = expected.split('and', 1)
        allowed.update(a+'and'+b for a in (left, '('+left+')') for b in (right, '('+right+')'))
    return check_rule(actual) in allowed


def validate_previous_schema(connection):
    try:
        inspector = sa.inspect(connection)
        dialect = connection.dialect.name
        require(dialect in GATE_DDL)
        require(set(inspector.get_table_names()) == set(PREVIOUS))
        require(not inspector.get_view_names())
        require(connection.execute(sa.text('SELECT version_num FROM alembic_version')).all() == [(down_revision,)])
        if dialect == 'sqlite':
            objects = connection.exec_driver_sql('SELECT type,name,sql FROM sqlite_master').all()
            require(all(kind in ('table', 'index') for kind, _, _ in objects))
            for kind, name, sql in objects:
                if kind == 'table':
                    require(canonical(sql) == canonical(SQLITE_PREVIOUS[name]))
            require(connection.exec_driver_sql('PRAGMA integrity_check').all() == [('ok',)])
            require(not connection.exec_driver_sql('PRAGMA foreign_key_check').all())
        else:
            version = connection.execute(sa.text('SELECT VERSION()')).scalar_one()
            parts = re.match(r'^(\d+)\.(\d+)\.(\d+)', version)
            require(parts is not None and tuple(map(int, parts.groups())) >= (8, 0, 16)
                    and 'MariaDB' not in version)
            charset = connection.execute(sa.text(
                'SELECT DEFAULT_CHARACTER_SET_NAME FROM information_schema.SCHEMATA '
                'WHERE SCHEMA_NAME=DATABASE()')).scalar_one()
            require(charset == 'utf8mb4')
            for kind, field in [('TRIGGERS', 'TRIGGER_SCHEMA'), ('ROUTINES', 'ROUTINE_SCHEMA'),
                                ('EVENTS', 'EVENT_SCHEMA')]:
                require(connection.execute(sa.text(
                    f'SELECT COUNT(*) FROM information_schema.{kind} WHERE {field}=DATABASE()')).scalar_one() == 0)
            tables = connection.execute(sa.text(
                "SELECT t.ENGINE,c.CHARACTER_SET_NAME FROM information_schema.TABLES t "
                "JOIN information_schema.COLLATION_CHARACTER_SET_APPLICABILITY c "
                "ON t.TABLE_COLLATION=c.COLLATION_NAME WHERE t.TABLE_SCHEMA=DATABASE()"
            )).all()
            require(len(tables) == len(PREVIOUS) and all(r == ('InnoDB', 'utf8mb4') for r in tables))
        for name, expected in PREVIOUS.items():
            columns = inspector.get_columns(name)
            require([c['name'] for c in columns] == [c[0] for c in expected['columns']])
            for column, (_, kind, nullable) in zip(columns, expected['columns']):
                actual = str(column['type'].compile(dialect=connection.dialect))
                allowed = {kind}
                if kind == 'BOOLEAN':
                    allowed.update(('TINYINT(1)', 'BOOL'))
                if kind == 'INTEGER':
                    allowed.add('INTEGER(11)')
                if (name, column['name']) in (('chat_turns', 'status'), ('chat_messages', 'role')):
                    allowed.update(('VARCHAR(16) COLLATE utf8mb4_bin',
                                    'VARCHAR(16) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin'))
                require(actual in allowed and column['nullable'] == nullable and column.get('default') is None
                        and not column.get('computed') and not column.get('identity'))
            if dialect == 'mysql':
                require(all(bool(c.get('autoincrement')) == (c['name'] == 'id' and name != 'conversations')
                            for c in columns))
            require(inspector.get_pk_constraint(name)['constrained_columns'] == expected['pk'])
            indexes = {i['name']: (bool(i['unique']), i['column_names']) for i in inspector.get_indexes(name)}
            wanted = {n: (bool(u), cols) for n, u, cols in expected['indexes']}
            uniques = {n: cols for n, cols in expected['uniques']}
            if dialect == 'mysql':
                wanted.update({n: (True, cols) for n, cols in uniques.items()})
                uniques.update({n: cols for n, u, cols in expected['indexes'] if u})
            require(indexes == wanted)
            require({u['name']: u['column_names'] for u in inspector.get_unique_constraints(name)} == uniques)
            require(all(not i.get('column_sorting') and not any(i.get('dialect_options', {}).values())
                        for i in inspector.get_indexes(name)))
            checks = {c['name']: c['sqltext'] for c in inspector.get_check_constraints(name)}
            require(set(checks) == {n for n, _ in expected['checks']})
            require(all(valid_check(checks[n], rule) for n, rule in expected['checks']))
            foreign = inspector.get_foreign_keys(name)
            require(len(foreign) == len(expected['foreign']))
            for fk, (key, cols, parent, target, opts) in zip(foreign, expected['foreign']):
                require(fk['constrained_columns'] == cols and fk['referred_table'] == parent
                        and fk['referred_columns'] == target and fk.get('referred_schema') in (
                            None, inspector.default_schema_name))
                require(fk['name'] == (key if key is not None or dialect == 'sqlite' else name+'_ibfk_1'))
                action = fk.get('options', {}).get('ondelete') or 'RESTRICT'
                require(action in ('RESTRICT', 'NO ACTION') if opts.get('ondelete') in (None, 'RESTRICT')
                        else action == opts['ondelete'])
                require(fk.get('options', {}).get('onupdate') in (None, 'RESTRICT', 'NO ACTION')
                        and not fk.get('options', {}).get('deferrable')
                        and fk.get('options', {}).get('initially') is None)
                child = cols[0]
                require(connection.execute(sa.text(
                    f'SELECT COUNT(*) FROM {name} c LEFT JOIN {parent} p ON c.{child}=p.{target[0]} '
                    f'WHERE c.{child} IS NOT NULL AND p.{target[0]} IS NULL')).scalar_one() == 0)
    except Exception:
        raise RuntimeError('agent_gate_migration_preflight_failed') from None


def upgrade():
    connection = op.get_bind()
    validate_previous_schema(connection)
    op.execute(sa.text(GATE_DDL[connection.dialect.name]))
    op.execute(sa.text("INSERT INTO agent_admission_gate (gate_key,lock_version) VALUES ('global',0)"))


def downgrade():
    raise RuntimeError('forward_migration_downgrade_requires_backup_restore')
