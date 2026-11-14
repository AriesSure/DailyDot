"""Add bounded conversation persistence; no Agent execution changes."""
import re
from datetime import date, datetime, time
import sqlalchemy as sa
from alembic import op

revision = '716951abf77d'
down_revision = "9a097b8e080b"
branch_labels = None
depends_on = None

LEGACY = {
    'cards': {
        'columns': {
            'date': [
                'DATE',
                False,
            ],
            'habits_completed': [
                'INTEGER',
                False,
            ],
            'id': [
                'INTEGER',
                False,
            ],
            'image_url': [
                'VARCHAR(255)',
                True,
            ],
            'quote': [
                'VARCHAR(255)',
                True,
            ],
            'streak': [
                'INTEGER',
                False,
            ],
            'user_id': [
                'INTEGER',
                False,
            ],
            'weekday': [
                'VARCHAR(10)',
                True,
            ],
        },
        'foreign': [
            [
                [
                    'user_id',
                ],
                'users',
                [
                    'id',
                ],
            ],
        ],
        'indexes': {
            'ix_cards_user_id': [
                False,
                [
                    'user_id',
                ],
            ],
        },
        'uniques': {
            'uix_date_user': [
                'date',
                'user_id',
            ],
        },
    },
    'habits': {
        'columns': {
            'frequency': [
                'VARCHAR(64)',
                True,
            ],
            'habit_name': [
                'VARCHAR(64)',
                False,
            ],
            'icon': [
                'VARCHAR(32)',
                False,
            ],
            'id': [
                'INTEGER',
                False,
            ],
            'note': [
                'VARCHAR(1024)',
                True,
            ],
            'time_period': [
                'VARCHAR(64)',
                True,
            ],
            'user_id': [
                'INTEGER',
                False,
            ],
        },
        'foreign': [
            [
                [
                    'user_id',
                ],
                'users',
                [
                    'id',
                ],
            ],
        ],
        'indexes': {
            'ix_habits_habit_name': [
                False,
                [
                    'habit_name',
                ],
            ],
            'ix_habits_user_id': [
                False,
                [
                    'user_id',
                ],
            ],
        },
        'uniques': {
        },
    },
    'records': {
        'columns': {
            'checkin_date': [
                'DATE',
                False,
            ],
            'checkin_time': [
                'TIME',
                True,
            ],
            'habit_id': [
                'INTEGER',
                False,
            ],
            'id': [
                'INTEGER',
                False,
            ],
            'note': [
                'VARCHAR(1024)',
                True,
            ],
        },
        'foreign': [
            [
                [
                    'habit_id',
                ],
                'habits',
                [
                    'id',
                ],
            ],
        ],
        'indexes': {
            'ix_records_habit_id': [
                False,
                [
                    'habit_id',
                ],
            ],
        },
        'uniques': {
            'uix_date_habit': [
                'checkin_date',
                'habit_id',
            ],
        },
    },
    'todos': {
        'columns': {
            'completed': [
                'BOOL',
                False,
            ],
            'created_at': [
                'DATETIME',
                False,
            ],
            'date': [
                'VARCHAR(16)',
                False,
            ],
            'event': [
                'VARCHAR(64)',
                False,
            ],
            'id': [
                'INTEGER',
                False,
            ],
            'note': [
                'VARCHAR(1024)',
                True,
            ],
            'time': [
                'VARCHAR(16)',
                False,
            ],
            'user_id': [
                'INTEGER',
                False,
            ],
        },
        'foreign': [
            [
                [
                    'user_id',
                ],
                'users',
                [
                    'id',
                ],
            ],
        ],
        'indexes': {
            'ix_todos_event': [
                False,
                [
                    'event',
                ],
            ],
            'ix_todos_user_id': [
                False,
                [
                    'user_id',
                ],
            ],
        },
        'uniques': {
        },
    },
    'users': {
        'columns': {
            'email': [
                'VARCHAR(120)',
                False,
            ],
            'id': [
                'INTEGER',
                False,
            ],
            'password_hash': [
                'VARCHAR(256)',
                True,
            ],
            'username': [
                'VARCHAR(64)',
                False,
            ],
        },
        'foreign': [],
        'indexes': {
            'ix_users_email': [
                True,
                [
                    'email',
                ],
            ],
            'ix_users_username': [
                True,
                [
                    'username',
                ],
            ],
        },
        'uniques': {
        },
    },
}
SQLITE_LEGACY = {
    'alembic_version': [
        (
            'createtablealembic_version(version_numvarchar(32)notnull,constraint'
            'alembic_version_pkcprimarykey(version_num))'
        ),
    ],
    'cards': [
        (
            'createtablecards(idintegernotnull,datedatenotnull,user_idintegernot'
            'null,image_urlvarchar(255),quotevarchar(255),weekdayvarchar(10),str'
            'eakintegernotnull,habits_completedintegernotnull,primarykey(id),for'
            'eignkey(user_id)referencesusers(id),constraintuix_date_userunique(d'
            'ate,user_id))'
        ),
        (
            'createtablecards(idintegernotnull,datedatenotnull,user_idintegernot'
            'null,image_urlvarchar(255),quotevarchar(255),weekdayvarchar(10),str'
            'eakintegernotnull,habits_completedintegernotnull,primarykey(id),con'
            'straintuix_date_userunique(date,user_id),foreignkey(user_id)referen'
            'cesusers(id))'
        ),
    ],
    'habits': [
        (
            'createtablehabits(idintegernotnull,habit_namevarchar(64)notnull,ico'
            'nvarchar(32)notnull,frequencyvarchar(64),time_periodvarchar(64),not'
            'evarchar(1024),user_idintegernotnull,primarykey(id),foreignkey(user'
            '_id)referencesusers(id))'
        ),
    ],
    'records': [
        (
            'createtablerecords(idintegernotnull,checkin_datedatenotnull,habit_i'
            'dintegernotnull,notevarchar(1024),checkin_timetime,primarykey(id),f'
            'oreignkey(habit_id)referenceshabits(id),constraintuix_date_habituni'
            'que(checkin_date,habit_id))'
        ),
        (
            'createtablerecords(idintegernotnull,checkin_datedatenotnull,habit_i'
            'dintegernotnull,notevarchar(1024),checkin_timetime,primarykey(id),c'
            'onstraintuix_date_habitunique(checkin_date,habit_id),foreignkey(hab'
            'it_id)referenceshabits(id))'
        ),
    ],
    'todos': [
        (
            'createtabletodos(idintegernotnull,eventvarchar(64)notnull,datevarch'
            'ar(16)notnull,timevarchar(16)notnull,notevarchar(1024),created_atda'
            'tetimenotnull,user_idintegernotnull,completedbooleannotnull,primary'
            'key(id),foreignkey(user_id)referencesusers(id))'
        ),
    ],
    'users': [
        (
            'createtableusers(idintegernotnull,usernamevarchar(64)notnull,emailv'
            'archar(120)notnull,password_hashvarchar(256),primarykey(id))'
        ),
    ],
}
DDL = {
    'sqlite': {
        'conversations': (
            '\nCREATE TABLE conversations (\n\tid VARCHAR(32) NOT NULL, \n\tuser_id I'
            'NTEGER NOT NULL, \n\ttitle VARCHAR(120) NOT NULL, \n\tcreated_at DATETI'
            'ME NOT NULL, \n\tupdated_at DATETIME NOT NULL, \n\tnext_turn_no INTEGER'
            ' NOT NULL, \n\tactive_request_id VARCHAR(32), \n\tactive_started_at DAT'
            'ETIME, \n\tarchived_at DATETIME, \n\tCONSTRAINT pk_conversations PRIMAR'
            'Y KEY (id), \n\tCONSTRAINT fk_conversations_user FOREIGN KEY(user_id)'
            ' REFERENCES users (id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_conversa'
            'tions_next_turn CHECK (next_turn_no >= 1)\n)\n\n'
        ),
        'chat_turns': (
            '\nCREATE TABLE chat_turns (\n\tid INTEGER NOT NULL, \n\tconversation_id '
            'VARCHAR(32) NOT NULL, \n\tturn_no INTEGER NOT NULL, \n\tclient_request_'
            'id VARCHAR(32) NOT NULL, \n\trequest_digest VARCHAR(64) NOT NULL, \n\ts'
            'tatus VARCHAR(16) COLLATE "BINARY" NOT NULL, \n\tcreated_at DATETIME '
            'NOT NULL, \n\tfinished_at DATETIME, \n\terror_code VARCHAR(64), \n\tCONST'
            'RAINT pk_chat_turns PRIMARY KEY (id), \n\tCONSTRAINT fk_chat_turns_co'
            'nversation FOREIGN KEY(conversation_id) REFERENCES conversations (i'
            'd) ON DELETE CASCADE, \n\tCONSTRAINT uq_chat_turns_number UNIQUE (con'
            'versation_id, turn_no), \n\tCONSTRAINT uq_chat_turns_request UNIQUE ('
            'conversation_id, client_request_id), \n\tCONSTRAINT ck_chat_turns_sta'
            "tus CHECK (status IN ('pending','completed','failed','interrupted')"
            ' AND length(status) = length(trim(status))), \n\tCONSTRAINT ck_chat_t'
            'urns_number CHECK (turn_no >= 1)\n)\n\n'
        ),
        'chat_messages': (
            '\nCREATE TABLE chat_messages (\n\tid INTEGER NOT NULL, \n\tturn_id INTEG'
            'ER NOT NULL, \n\tposition INTEGER NOT NULL, \n\trole VARCHAR(16) COLLAT'
            'E "BINARY" NOT NULL, \n\tcontent TEXT, \n\ttool_calls JSON, \n\ttool_call'
            '_id VARCHAR(100), \n\ttool_name VARCHAR(64), \n\tcreated_at DATETIME NO'
            'T NULL, \n\tCONSTRAINT pk_chat_messages PRIMARY KEY (id), \n\tCONSTRAIN'
            'T fk_chat_messages_turn FOREIGN KEY(turn_id) REFERENCES chat_turns '
            '(id) ON DELETE CASCADE, \n\tCONSTRAINT uq_chat_messages_position UNIQ'
            'UE (turn_id, position), \n\tCONSTRAINT ck_chat_messages_role CHECK (r'
            "ole IN ('user','assistant','tool') AND length(role) = length(trim(r"
            'ole))), \n\tCONSTRAINT ck_chat_messages_position CHECK (position >= 0'
            ')\n)\n\n'
        ),
    },
    'mysql': {
        'conversations': (
            '\nCREATE TABLE conversations (\n\tid VARCHAR(32) NOT NULL, \n\tuser_id'
            ' INTEGER NOT NULL, \n\ttitle VARCHAR(120) NOT NULL, \n\tcreated_at DA'
            'TETIME NOT NULL, \n\tupdated_at DATETIME NOT NULL, \n\tnext_turn_no I'
            'NTEGER NOT NULL, \n\tactive_request_id VARCHAR(32), \n\tactive_starte'
            'd_at DATETIME, \n\tarchived_at DATETIME, \n\tCONSTRAINT pk_conversati'
            'ons PRIMARY KEY (id), \n\tCONSTRAINT fk_conversations_user FOREIGN '
            'KEY(user_id) REFERENCES users (id) ON DELETE RESTRICT, \n\tCONSTRAI'
            'NT ck_conversations_next_turn CHECK (next_turn_no >= 1)\n) ENGINE='
            'InnoDB\n'
        ),
        'chat_turns': (
            '\nCREATE TABLE chat_turns (\n\tid INTEGER NOT NULL AUTO_INCREMENT, \n'
            '\tconversation_id VARCHAR(32) NOT NULL, \n\tturn_no INTEGER NOT NULL'
            ', \n\tclient_request_id VARCHAR(32) NOT NULL, \n\trequest_digest VARC'
            'HAR(64) NOT NULL, \n\tstatus VARCHAR(16) COLLATE utf8mb4_bin NOT NU'
            'LL, \n\tcreated_at DATETIME NOT NULL, \n\tfinished_at DATETIME, \n\terr'
            'or_code VARCHAR(64), \n\tCONSTRAINT pk_chat_turns PRIMARY KEY (id),'
            ' \n\tCONSTRAINT fk_chat_turns_conversation FOREIGN KEY(conversation'
            '_id) REFERENCES conversations (id) ON DELETE CASCADE, \n\tCONSTRAIN'
            'T uq_chat_turns_number UNIQUE (conversation_id, turn_no), \n\tCONST'
            'RAINT uq_chat_turns_request UNIQUE (conversation_id, client_reque'
            "st_id), \n\tCONSTRAINT ck_chat_turns_status CHECK (status IN ('pend"
            "ing','completed','failed','interrupted') AND length(status) = len"
            'gth(trim(status))), \n\tCONSTRAINT ck_chat_turns_number CHECK (turn'
            '_no >= 1)\n) ENGINE=InnoDB\n'
        ),
        'chat_messages': (
            '\nCREATE TABLE chat_messages (\n\tid INTEGER NOT NULL AUTO_INCREMENT'
            ', \n\tturn_id INTEGER NOT NULL, \n\tposition INTEGER NOT NULL, \n\t`rol'
            'e` VARCHAR(16) COLLATE utf8mb4_bin NOT NULL, \n\tcontent TEXT, \n\tto'
            'ol_calls JSON, \n\ttool_call_id VARCHAR(100), \n\ttool_name VARCHAR(6'
            '4), \n\tcreated_at DATETIME NOT NULL, \n\tCONSTRAINT pk_chat_messages'
            ' PRIMARY KEY (id), \n\tCONSTRAINT fk_chat_messages_turn FOREIGN KEY'
            '(turn_id) REFERENCES chat_turns (id) ON DELETE CASCADE, \n\tCONSTRA'
            'INT uq_chat_messages_position UNIQUE (turn_id, position), \n\tCONST'
            "RAINT ck_chat_messages_role CHECK (role IN ('user','assistant','t"
            "ool') AND length(role) = length(trim(role))), \n\tCONSTRAINT ck_cha"
            't_messages_position CHECK (position >= 0)\n) ENGINE=InnoDB\n'
        ),
    },
}
INDEX_DDL = {
    'sqlite': [
        (
            'CREATE INDEX ix_conversations_owner_updated ON conversations (user_'
            'id, updated_at, id)'
        ),
        'CREATE INDEX ix_chat_turns_conversation ON chat_turns (conversation_id)',
        'CREATE INDEX ix_chat_messages_turn ON chat_messages (turn_id)',
    ],
    'mysql': [
        (
            'CREATE INDEX ix_conversations_owner_updated ON conversations (user_'
            'id, updated_at, id)'
        ),
        'CREATE INDEX ix_chat_turns_conversation ON chat_turns (conversation_id)',
        'CREATE INDEX ix_chat_messages_turn ON chat_messages (turn_id)',
    ],
}


def _require(ok):
    if not ok:
        raise RuntimeError('conversation_migration_preflight_failed')


LEGACY_ORDER = {
    'alembic_version': [
        'version_num',
    ],
    'cards': [
        'id',
        'date',
        'user_id',
        'image_url',
        'quote',
        'weekday',
        'streak',
        'habits_completed',
    ],
    'habits': [
        'id',
        'habit_name',
        'icon',
        'frequency',
        'time_period',
        'note',
        'user_id',
    ],
    'records': [
        'id',
        'checkin_date',
        'habit_id',
        'note',
        'checkin_time',
    ],
    'todos': [
        'id',
        'event',
        'date',
        'time',
        'note',
        'created_at',
        'user_id',
        'completed',
    ],
    'users': [
        'id',
        'username',
        'email',
        'password_hash',
    ],
}


def _check_previous_schema(connection):
    """Full read-only old-head preflight, before the first business DDL."""
    inspector = sa.inspect(connection)
    dialect = connection.dialect.name
    _require(dialect in ('sqlite', 'mysql'))
    _require(set(inspector.get_table_names()) == set(LEGACY) | {'alembic_version'})
    _require(not inspector.get_view_names())
    _require(connection.execute(sa.text('SELECT version_num FROM alembic_version')).all() == [(down_revision,)])
    if dialect == 'sqlite':
        objects = connection.execute(sa.text('SELECT type,name,tbl_name,sql FROM sqlite_master')).all()
        _require(all(kind in ('table', 'index') for kind, _, _, _ in objects))
        for kind, name, _, sql in objects:
            if kind == 'table':
                canonical = re.sub(r'\s+', '', sql).replace('"', '').lower()
                _require(canonical in SQLITE_LEGACY.get(name, []))
        _require(connection.exec_driver_sql('PRAGMA integrity_check').all() == [('ok',)])
        _require(not connection.exec_driver_sql('PRAGMA foreign_key_check').all())
    else:
        version = connection.execute(sa.text('SELECT VERSION()')).scalar_one()
        parts = re.match(r'^(\d+)\.(\d+)\.(\d+)', version)
        _require(parts is not None and tuple(map(int, parts.groups())) >= (8, 0, 16)
                 and 'MariaDB' not in version)
        charset = connection.execute(sa.text(
            'SELECT DEFAULT_CHARACTER_SET_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=DATABASE()'
        )).scalar_one()
        _require(charset == 'utf8mb4')
        validate_innodb_parent(connection)
        for kind in ('TRIGGERS', 'ROUTINES', 'EVENTS'):
            column = {'TRIGGERS': 'TRIGGER_SCHEMA', 'ROUTINES': 'ROUTINE_SCHEMA', 'EVENTS': 'EVENT_SCHEMA'}[kind]
            _require(connection.execute(sa.text(
                f'SELECT COUNT(*) FROM information_schema.{kind} WHERE {column}=DATABASE()')).scalar_one() == 0)
    version = inspector.get_columns('alembic_version')
    _require(len(version) == 1 and version[0]['name'] == 'version_num'
             and isinstance(version[0]['type'], sa.String) and version[0]['type'].length == 32
             and not version[0]['nullable'] and version[0].get('default') is None)
    _require(inspector.get_pk_constraint('alembic_version')['constrained_columns'] == ['version_num'])
    _require(not inspector.get_indexes('alembic_version') and not inspector.get_check_constraints('alembic_version')
             and not inspector.get_foreign_keys('alembic_version'))
    for name, expected in LEGACY.items():
        columns = inspector.get_columns(name)
        _require([c['name'] for c in columns] == LEGACY_ORDER[name])
        for c in columns:
            kind, nullable = expected['columns'][c['name']]
            actual = str(c['type'].compile(dialect=connection.dialect))
            allowed = {'BOOLEAN', 'TINYINT(1)', 'BOOL'} if kind in ('BOOL', 'BOOLEAN') else {kind}
            if kind == 'INTEGER':
                allowed.add('INTEGER(11)')
            _require(actual in allowed and c['nullable'] == nullable and c.get('default') is None
                     and not c.get('computed') and not c.get('identity'))
        for row in connection.execute(sa.text(f'SELECT * FROM {name}')).mappings():
            for key, (kind, nullable) in expected['columns'].items():
                value = row[key]
                if value is None:
                    _require(nullable)
                elif kind in ('BOOL', 'BOOLEAN'):
                    _require(type(value) in (bool, int) and value in (0, 1))
                elif kind.startswith('VARCHAR'):
                    _require(isinstance(value, str) and len(value) <= int(kind[8:-1]))
                elif kind == 'DATE':
                    _require(type(value) is date or (isinstance(value, str) and date.fromisoformat(value)))
                elif kind == 'DATETIME':
                    _require(type(value) is datetime or (isinstance(value, str) and datetime.fromisoformat(value)))
                elif kind == 'TIME':
                    # PyMySQL returns TIME as timedelta. String parsing catches SQLite malformed values.
                    if isinstance(value, str):
                        time.fromisoformat(value)
        _require(inspector.get_pk_constraint(name)['constrained_columns'] == ['id'])
        _require(not inspector.get_check_constraints(name))
        indexes = {i['name']: [bool(i['unique']), i['column_names']] for i in inspector.get_indexes(name)}
        wanted = dict(expected['indexes'])
        if dialect == 'mysql':
            wanted.update({key: [True, cols] for key, cols in expected['uniques'].items()})
        _require(indexes == wanted)
        _require(all(not i.get('column_sorting') and not any(i.get('dialect_options', {}).values())
                     for i in inspector.get_indexes(name)))
        uniques = {u['name']: u['column_names'] for u in inspector.get_unique_constraints(name)}
        wanted_uniques = dict(expected['uniques'])
        if dialect == 'mysql':
            wanted_uniques.update({key: cols for key, (unique, cols) in expected['indexes'].items() if unique})
        _require(uniques == wanted_uniques)
        foreign = inspector.get_foreign_keys(name)
        _require(sorted([[f['constrained_columns'], f['referred_table'], f['referred_columns']] for f in foreign])
                 == sorted(expected['foreign']))
        for f in foreign:
            _require(f.get('referred_schema') in (None, inspector.default_schema_name))
            opts = f.get('options', {})
            _require(all(opts.get(k) in (None, 'RESTRICT', 'NO ACTION') for k in ('ondelete', 'onupdate'))
                     and not opts.get('deferrable') and opts.get('initially') is None)
            child, parent, key = f['constrained_columns'][0], f['referred_table'], f['referred_columns'][0]
            _require(connection.execute(sa.text(
                f'SELECT COUNT(*) FROM {name} c LEFT JOIN {parent} p ON c.{child}=p.{key} '
                f'WHERE c.{child} IS NOT NULL AND p.{key} IS NULL')).scalar_one() == 0)


def validate_innodb_parent(connection):
    count = connection.execute(sa.text(
        "SELECT COUNT(*) FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='users' AND ENGINE='InnoDB'"
    )).scalar_one()
    _require(count == 1)


def validate_previous_schema(connection):
    try:
        _check_previous_schema(connection)
    except Exception:
        raise RuntimeError('conversation_migration_preflight_failed') from None


def upgrade():
    validate_previous_schema(op.get_bind())
    dialect = op.get_bind().dialect.name
    for statement in DDL[dialect].values():
        op.execute(sa.text(statement))
    for statement in INDEX_DDL[dialect]:
        op.execute(sa.text(statement))


def downgrade():
    raise RuntimeError('forward_migration_downgrade_requires_backup_restore')
