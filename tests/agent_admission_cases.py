"""Shared real-connection scenarios for owned SQLite and fixed MySQL CI only."""
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import datetime, timedelta
import hashlib
from threading import Barrier, Lock
from uuid import uuid4

from flask_login import login_user
import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.agent import conversations as c, admission
from app.models import User, Conversation, ChatTurn, ChatMessage


OUTCOMES = ('created', 'duplicate', 'agent_user_busy', 'agent_global_capacity',
            'agent_rate_limited', 'agent_admission_unavailable', 'other')
ASSERTIONS = {
    'independent_connections': 'connections', 'same_user_one_winner': 'same_user',
    'global_initial_allowed': 'global', 'global_capacity_one_winner': 'global',
    'duplicate_idempotency': 'duplicate', 'minute_initial_allowed': 'minute',
    'minute_window_one_winner': 'minute', 'minute_window_retry': 'minute',
    'hour_window_initial_limit': 'hour', 'hour_window_one_winner': 'hour', 'hour_window_retry': 'hour',
    'rollback_observed': 'rollback', 'rollback_no_turn': 'rollback', 'rollback_no_active': 'rollback',
    'rollback_retry_allowed': 'rollback', 'gate_released': 'global', 'time_probe_verified': 'time',
}

EXPECTED = {key: ['created'] for key in ASSERTIONS}
EXPECTED.update(same_user_one_winner=['created', 'agent_user_busy'],
                global_capacity_one_winner=['created', 'agent_global_capacity'],
                duplicate_idempotency=['duplicate', 'created'],
                minute_window_retry=['agent_rate_limited'], hour_window_initial_limit=['agent_rate_limited'],
                hour_window_retry=['agent_rate_limited'])


def outcome(value):
    if value is True:
        return 'created'
    if value is False:
        return 'duplicate'
    return value if type(value) is str and value in OUTCOMES else 'other'


class AdmissionDiagnosticError(AssertionError):
    def __init__(self, diagnostic):
        super().__init__('admission_assertion_failed')
        self.diagnostic = diagnostic


def bounded_int(value, low, high):
    return type(value) is int and low <= value <= high


PROBE_PHASES = ('unknown', 'connection_identity', 'server_metadata', 'column_metadata',
                'synthetic_user_insert', 'conversation_insert', 'turn_insert_250', 'turn_readback_250',
                'turn_insert_500', 'turn_readback_500', 'turn_insert_750', 'turn_readback_750',
                'transaction_rollback', 'rollback_user_check', 'rollback_conversation_check', 'result_validation')
FAILURE_KINDS = ('dbapi', 'sqlalchemy', 'identity', 'residue', 'validation', 'other')
PROBE_EXCEPTIONS = ('IntegrityError', 'OperationalError', 'ProgrammingError', 'DBAPIError',
                    'SQLAlchemyError', 'MigrationSafetyError', 'RuntimeError', 'ValueError', 'TypeError', 'other')


def probe_error(phase, exc=None, kind=None):
    import pymysql
    import re
    dbapi = exc.orig if isinstance(exc, sa.exc.DBAPIError) else exc
    real_dbapi = isinstance(dbapi, pymysql.MySQLError)
    errno = dbapi.args[0] if real_dbapi and dbapi.args else None
    state = getattr(dbapi, 'sqlstate', None) if real_dbapi else None
    name = type(exc).__name__ if exc is not None else 'other'
    category = ('dbapi' if real_dbapi else 'sqlalchemy' if isinstance(exc, sa.exc.SQLAlchemyError)
                else 'identity' if name == 'MigrationSafetyError' else 'other')
    return {'probe_phase': phase if type(phase) is str and phase in PROBE_PHASES else 'unknown',
            'failure_kind': kind if kind in FAILURE_KINDS else category,
            'exception_type': name if name in PROBE_EXCEPTIONS else 'other',
            'mysql_errno': errno if bounded_int(errno, 1, 65535) else None,
            'sqlstate': state if type(state) is str and re.fullmatch('[0-9A-Z]{5}', state) else None}


def probe_failure(phase, exc=None, kind=None):
    return {'verified': False, **probe_error(phase, exc, kind), 'rollback_failure': None}


def valid_probe_error(value):
    import re
    return (type(value) is dict and set(value) == {
        'probe_phase', 'failure_kind', 'exception_type', 'mysql_errno', 'sqlstate'}
        and type(value['probe_phase']) is str and value['probe_phase'] in PROBE_PHASES
        and type(value['failure_kind']) is str and value['failure_kind'] in FAILURE_KINDS
        and type(value['exception_type']) is str and value['exception_type'] in PROBE_EXCEPTIONS
        and (value['mysql_errno'] is None or bounded_int(value['mysql_errno'], 1, 65535))
        and (value['sqlstate'] is None or type(value['sqlstate']) is str
             and re.fullmatch('[0-9A-Z]{5}', value['sqlstate']) is not None))


def valid_time_diagnostic(value):
    if value is None:
        return True  # SQLite has no MySQL precision claim.
    if type(value) is not dict or type(value.get('verified')) is not bool:
        return False
    if not value['verified']:
        if set(value) != {'verified', 'probe_phase', 'failure_kind', 'exception_type',
                          'mysql_errno', 'sqlstate', 'rollback_failure'}:
            return False
        error = {key: value[key] for key in value if key not in ('verified', 'rollback_failure')}
        rollback = value['rollback_failure']
        return (valid_probe_error(error) and (rollback is None or valid_probe_error(rollback)
                and rollback['probe_phase'] == 'transaction_rollback'))
    if set(value) != {'verified', 'server_version', 'truncate_fractional', 'datetime_precision',
                      'utc_microsecond', 'samples', 'future_rows', 'max_future_us'}:
        return False
    import re
    if not (type(value['server_version']) is str and re.fullmatch(r'8\.0\.\d{1,3}', value['server_version'])
            and type(value['truncate_fractional']) is bool
            and bounded_int(value['datetime_precision'], 0, 6)
            and bounded_int(value['utc_microsecond'], 0, 999999)
            and (value['future_rows'] is None or bounded_int(value['future_rows'], 0, 1000000))
            and (value['max_future_us'] is None or bounded_int(value['max_future_us'], 0, 86400000000))):
        return False
    samples = value['samples']
    return (type(samples) is list and len(samples) == 3 and all(
        type(row) is dict and set(row) == {'input_us', 'read_us', 'delta_us', 'greater_than_input'}
        and row['input_us'] == expected and type(row['input_us']) is int
        and bounded_int(row['read_us'], 0, 999999) and bounded_int(row['delta_us'], -1000000, 1000000)
        and type(row['greater_than_input']) is bool
        for row, expected in zip(samples, (250000, 500000, 750000))))


WORKER_IDS = ('left', 'right')
WORKER_STAGES = ('unknown', 'request_context', 'login', 'connect', 'connection_id', 'identity',
                 'identity_rollback', 'barrier', 'session', 'begin', 'commit', 'rollback_probe',
                 'session_close', 'connection_close', 'request_close', 'done')
WORKER_EXCEPTIONS = PROBE_EXCEPTIONS + ('BrokenBarrierError', 'CancelledError', 'TimeoutError',
                                        'ConversationError', 'InvalidRequestError', 'PendingRollbackError',
                                        'ResourceClosedError', 'DetachedInstanceError', 'StatementError')


class WorkerTrace:
    """Per-worker diagnostic metadata only; this lock never controls admission."""
    def __init__(self, worker_id):
        if worker_id not in WORKER_IDS:
            raise ValueError('invalid_worker_id')
        self.worker_id = worker_id
        self._stage = 'unknown'
        self._lock = Lock()

    def mark(self, stage):
        with self._lock:
            self._stage = stage if stage in WORKER_STAGES else 'unknown'

    def stage(self):
        with self._lock:
            return self._stage


def wait_original(futures):
    """Keep the old per-Future 40s wait until first error; executor still joins.

    Subsequent inspection is separate and never waits. In particular, do not add
    another 40s wait for a peer after the first error or invent a total deadline.
    """
    timed_out = {}
    for future, trace in futures.items():
        try:
            future.result(timeout=40)
        except FutureTimeoutError as exc:
            # Completion may race with timeout reporting. A worker-raised
            # TimeoutError is the exact exception stored on a completed Future.
            worker_timeout = False
            if future.done() and not future.cancelled():
                worker_timeout = future.exception(timeout=0) is exc
            if not worker_timeout:
                timed_out[trace.worker_id] = trace.stage()
            break
        except Exception:
            break
    return timed_out


def worker_error(exc):
    value = probe_error('unknown', exc)
    value.pop('probe_phase')
    name = type(exc).__name__
    value['exception_type'] = name if name in WORKER_EXCEPTIONS else 'other'
    return value


def collect_futures(futures, timed_out):
    """Inspect every submitted Future, zero extra wait, no ordering-based blame."""
    records, values = [], {}
    for future, trace in futures.items():
        finished = future.done()
        row = {'worker_id': trace.worker_id, 'stage': trace.stage(),
               'wait_stage': timed_out.get(trace.worker_id), 'finished': finished,
               'status': 'pending', 'outcome': 'other', 'error': None}
        if future.cancelled():
            row['status'] = 'cancelled'
        elif finished:
            try:
                value = future.result(timeout=0)
                values[trace.worker_id] = value
                row['outcome'] = outcome(value[1])
                row['status'] = 'completed'
            except Exception as exc:
                row['status'], row['error'] = 'failed', worker_error(exc)
        if trace.worker_id in timed_out:
            row['status'] = 'timeout'  # Never turn a missed wait into success.
        records.append(row)
    return sorted(records, key=lambda row: row['worker_id']), values


def valid_workers(rows):
    if type(rows) is not list or len(rows) != 2:
        return False
    ids = []
    for row in rows:
        if type(row) is not dict or set(row) != {
                'worker_id', 'stage', 'wait_stage', 'finished', 'status', 'outcome', 'error'}:
            return False
        if not (type(row['worker_id']) is str and row['worker_id'] in WORKER_IDS
                and type(row['stage']) is str and row['stage'] in WORKER_STAGES
                and (row['wait_stage'] is None or type(row['wait_stage']) is str
                     and row['wait_stage'] in WORKER_STAGES)
                and type(row['finished']) is bool and type(row['status']) is str and row['status'] in (
                    'completed', 'failed', 'pending', 'timeout', 'cancelled')
                and type(row['outcome']) is str and row['outcome'] in OUTCOMES):
            return False
        if row['status'] in ('completed', 'failed', 'cancelled') and not row['finished']:
            return False
        if row['status'] == 'pending' and row['finished']:
            return False
        if (row['status'] == 'timeout') != (row['wait_stage'] is not None):
            return False
        if row['error'] is not None:
            error = row['error']
            if type(error) is not dict or set(error) != {
                    'exception_type', 'failure_kind', 'mysql_errno', 'sqlstate'}:
                return False
            if type(error['exception_type']) is not str or error['exception_type'] not in WORKER_EXCEPTIONS:
                return False
            safe = {**error, 'exception_type': 'other', 'probe_phase': 'unknown'}
            if not valid_probe_error(safe) or row['status'] not in ('failed', 'timeout') or not row['finished']:
                return False
        elif row['status'] == 'failed':
            return False
        if ((not row['finished'] or row['error'] is not None or row['status'] == 'cancelled')
                and row['outcome'] != 'other'):
            return False
        ids.append(row['worker_id'])
    return sorted(ids) == list(WORKER_IDS) and any(row['status'] != 'completed' for row in rows)


def valid_admission_diagnostic(value):
    fields = {'scenario_id', 'assertion_id', 'expected', 'actual', 'outcome_codes', 'time_precision'}
    if type(value) is not dict or set(value) not in (fields, fields | {'workers'}):
        return False
    key = value['assertion_id']
    return (type(key) is str and key in ASSERTIONS and value['scenario_id'] == ASSERTIONS[key]
            and all(type(value[k]) is list and 1 <= len(value[k]) <= 2
                    and all(type(v) is str and v in OUTCOMES for v in value[k])
                    for k in ('expected', 'actual', 'outcome_codes'))
            and value['expected'] == EXPECTED[key] and value['time_precision'] is not None
            and valid_time_diagnostic(value['time_precision'])
            and ('workers' not in value or valid_workers(value['workers'])
                 and value['actual'] == value['outcome_codes'] == [r['outcome'] for r in value['workers']]))


def check_probe_connection(connection, engine, active):
    if (connection.engine is not engine or connection.closed is not False
            or connection.invalidated is not False or connection.in_transaction() is not active):
        raise RuntimeError('probe_connection_state_invalid')


def mysql_time_probe(engine, verify_connection):
    """Dedicated synthetic rows, read back via SQL in the same transaction; always rollback."""
    if engine.dialect.name != 'mysql':
        return None
    uid = cid = None
    phase = 'connection_identity'
    failure = None
    try:
        with engine.connect() as connection:
            physical_connection = connection.connection.driver_connection
            connection.begin()
            check_probe_connection(connection, engine, True)
            if verify_connection:
                verify_connection(connection)
            connection.rollback()
            check_probe_connection(connection, engine, False)
            if connection.connection.driver_connection is not physical_connection:
                raise RuntimeError('probe_physical_connection_changed')
            with Session(connection) as session:
                try:
                    phase = 'server_metadata'
                    version, mode, now = session.execute(sa.text(
                        'SELECT VERSION(),@@SESSION.sql_mode,UTC_TIMESTAMP(6)')).one()
                    phase = 'column_metadata'
                    precision = session.execute(sa.text(
                        "SELECT DATETIME_PRECISION FROM information_schema.COLUMNS "
                        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='chat_turns' "
                        "AND COLUMN_NAME='created_at'")).scalar_one()
                    phase = 'synthetic_user_insert'
                    user = User(username=uuid4().hex, email=uuid4().hex + '@example.invalid')
                    session.add(user)
                    session.flush()
                    uid = user.id
                    phase = 'conversation_insert'
                    convo = Conversation(user_id=uid)
                    session.add(convo)
                    session.flush()
                    cid = convo.id
                    samples = []
                    for i, microsecond in enumerate((250000, 500000, 750000), 1):
                        phase = ('turn_insert_250', 'turn_insert_500', 'turn_insert_750')[i - 1]
                        stamp = datetime(2026, 1, 1, 12, 0, 0, microsecond)
                        row = ChatTurn(conversation_id=cid, turn_no=i, client_request_id=uuid4().hex,
                                       request_digest='a' * 64, created_at=stamp)
                        session.add(row)
                        session.flush()
                        phase = ('turn_readback_250', 'turn_readback_500', 'turn_readback_750')[i - 1]
                        actual, greater = session.execute(sa.text(
                            'SELECT created_at,created_at > :stamp FROM chat_turns WHERE id=:id'),
                            {'stamp': stamp, 'id': row.id}).one()
                        samples.append({'input_us': microsecond, 'read_us': actual.microsecond,
                                        'delta_us': int((actual - stamp).total_seconds() * 1000000),
                                        'greater_than_input': bool(greater)})
                    phase = 'result_validation'
                    result = {'verified': True, 'server_version': version,
                              'truncate_fractional': 'TIME_TRUNCATE_FRACTIONAL' in mode.split(','),
                              'datetime_precision': precision, 'utc_microsecond': now.microsecond,
                              'samples': samples, 'future_rows': None, 'max_future_us': None}
                except Exception as exc:
                    failure = probe_failure(phase, exc)
                finally:
                    phase = 'transaction_rollback'
                    try:
                        session.rollback()
                        check_probe_connection(connection, engine, False)
                        if connection.connection.driver_connection is not physical_connection:
                            raise RuntimeError('probe_physical_connection_changed')
                    except Exception as exc:
                        rollback = probe_error(phase, exc)
                        if failure is None:
                            failure = probe_failure(phase, exc)
                        failure['rollback_failure'] = rollback
            if failure is not None:
                return failure
            # Keep the original checked out: residue reads use another physical connection.
            phase = 'rollback_user_check'
            with engine.connect() as residue:
                if (residue is connection or residue.connection.driver_connection
                        is connection.connection.driver_connection):
                    raise RuntimeError('probe_residue_connection_not_independent')
                residue.begin()
                try:
                    check_probe_connection(residue, engine, True)
                    if verify_connection:
                        verify_connection(residue)
                    if residue.execute(sa.select(sa.func.count()).select_from(User).where(User.id == uid)).scalar_one():
                        failure = probe_failure(phase, kind='residue')
                    else:
                        phase = 'rollback_conversation_check'
                        if residue.execute(sa.select(sa.func.count()).select_from(Conversation).where(
                                Conversation.id == cid)).scalar_one():
                            failure = probe_failure(phase, kind='residue')
                except Exception as exc:
                    failure = probe_failure(phase, exc)
                finally:
                    try:
                        residue.rollback()
                        check_probe_connection(residue, engine, False)
                    except Exception as exc:
                        if failure is None:
                            failure = probe_failure('transaction_rollback', exc)
                        failure['rollback_failure'] = probe_error('transaction_rollback', exc)
            if failure is not None:
                return failure
        phase = 'result_validation'
        return result if valid_time_diagnostic(result) else probe_failure(phase, kind='validation')
    except Exception as exc:
        return failure if failure is not None else probe_failure(phase, exc)


def future_time_probe(engine, uid, diagnostic, verify_connection):
    if diagnostic is None or diagnostic.get('verified') is False:
        return diagnostic
    result = dict(diagnostic)
    if uid is None:
        return result  # No uniquely identified user: leave observations unknown.
    try:
        with engine.connect() as connection:
            connection.begin()
            check_probe_connection(connection, engine, True)
            try:
                if verify_connection:
                    verify_connection(connection)
                count, delta = connection.execute(sa.text(
                    'SELECT COUNT(*),COALESCE(MAX(TIMESTAMPDIFF(MICROSECOND,UTC_TIMESTAMP(6),t.created_at)),0) '
                    'FROM chat_turns t JOIN conversations c ON c.id=t.conversation_id '
                    'WHERE c.user_id=:uid AND t.created_at>UTC_TIMESTAMP(6)'), {'uid': uid}).one()
            finally:
                connection.rollback()
            check_probe_connection(connection, engine, False)
        result['future_rows'] = count if bounded_int(count, 0, 1000000) else None
        result['max_future_us'] = delta if bounded_int(delta, 0, 86400000000) else None
    except Exception:
        result['future_rows'] = result['max_future_us'] = None
    return result


def verify_concurrency(application, engine, *, verify_connection=None):
    from app.extensions import login
    if not hasattr(application, 'login_manager'):
        login.init_app(application)
    application.config['SECRET_KEY'] = 'synthetic-admission-test-only'
    digest = hashlib.sha256(b'question').hexdigest()
    physical = set()
    precision = mysql_time_probe(engine, verify_connection)

    def check(condition, assertion_id, values, expected, uid=None):
        if not condition:
            predicates = {'independent_connections', 'rollback_observed', 'rollback_no_turn',
                          'rollback_no_active', 'time_probe_verified'}
            codes = ([('created' if value is True else 'other') for value in values]
                     if assertion_id in predicates else [outcome(value) for value in values])
            diagnostic = {'scenario_id': ASSERTIONS[assertion_id], 'assertion_id': assertion_id,
                          'expected': expected, 'actual': codes, 'outcome_codes': codes,
                          'time_precision': future_time_probe(engine, uid, precision, verify_connection)}
            raise AdmissionDiagnosticError(diagnostic)

    check(precision is None or precision.get('verified') is True,
          'time_probe_verified', [False], ['created'])

    def scenario_users(n):
        with Session(engine, expire_on_commit=False) as session, session.begin():
            users = [User(username=uuid4().hex, email=uuid4().hex + '@example.invalid') for _ in range(n)]
            session.add_all(users)
            session.flush()
            conversations = [Conversation(user_id=user.id) for user in users]
            session.add_all(conversations)
            session.flush()
            return users, [row.id for row in conversations]

    def attempt(user, cid, rid, barrier=None, *, rollback=False, trace=None):
        def mark(stage):
            if trace is not None:
                trace.mark(stage)
        mark('request_context')
        with application.test_request_context():
            mark('login')
            login_user(user)
            mark('connect')
            with engine.connect() as connection:
                mark('connection_id')
                identifier = (connection.execute(sa.text('SELECT CONNECTION_ID()')).scalar_one()
                              if engine.dialect.name == 'mysql' else id(connection.connection.driver_connection))
                mark('identity')
                if verify_connection:
                    verify_connection(connection)
                mark('identity_rollback')
                connection.rollback()
                if barrier:
                    mark('barrier')
                    barrier.wait(timeout=20)
                try:
                    mark('session')
                    with Session(connection, expire_on_commit=False) as session:
                        mark('begin')
                        with session.begin():
                            result = c._begin_with_session(
                                session, user.id, cid, rid, 'question', digest, admitted=True)
                            if rollback:
                                mark('rollback_probe')
                                raise RuntimeError('synthetic_transaction_rollback')
                            mark('commit')
                        mark('session_close')
                    value = result['created']
                except c.ConversationError as exc:
                    value = exc.args[0]
                mark('connection_close')
            mark('request_close')
        mark('done')
        return identifier, value

    def race(jobs, assertion_id):
        barrier = Barrier(len(jobs))
        with ThreadPoolExecutor(len(jobs)) as executor:
            futures = {}
            for worker_id, job in jobs.items():
                trace = WorkerTrace(worker_id)
                futures[executor.submit(attempt, *job, barrier, trace=trace)] = trace
            timed_out = wait_original(futures)
        # The original executor exit already joins all workers; no new wait here.
        workers, results = collect_futures(futures, timed_out)
        if any(row['status'] != 'completed' for row in workers):
            codes = [row['outcome'] for row in workers]
            raise AdmissionDiagnosticError({
                'scenario_id': ASSERTIONS[assertion_id], 'assertion_id': assertion_id,
                'expected': EXPECTED[assertion_id], 'actual': codes, 'outcome_codes': codes,
                'time_precision': precision, 'workers': workers})
        values = [results[worker_id] for worker_id in jobs]
        check(len({row[0] for row in values}) == len(jobs), 'independent_connections',
              [len({row[0] for row in values}) == len(jobs)], ['created'])
        physical.update(row[0] for row in values)
        return [row[1] for row in values]

    def finish(user, cid, rid):
        with application.test_request_context():
            login_user(user)
            c.finish_turn(cid, rid, status='failed', messages=[], error_code='chat_execution_failed')

    users, ids = scenario_users(1)
    with Session(engine, expire_on_commit=False) as session, session.begin():
        other = Conversation(user_id=users[0].id)
        session.add(other)
        session.flush()
        ids.append(other.id)
    requests = [uuid4().hex, uuid4().hex]
    outcomes = race({'left': (users[0], ids[0], requests[0]),
                     'right': (users[0], ids[1], requests[1])}, 'same_user_one_winner')
    check(outcomes.count(True) == 1 and outcomes.count('agent_user_busy') == 1,
          'same_user_one_winner', outcomes, ['created', 'agent_user_busy'], users[0].id)
    for i, race_outcome in enumerate(outcomes):
        if race_outcome is True:
            finish(users[0], ids[i], requests[i])

    users, ids = scenario_users(3)
    requests = [uuid4().hex for _ in users]
    value = attempt(users[0], ids[0], requests[0])[1]
    check(value is True, 'global_initial_allowed', [value], ['created'], users[0].id)
    # The first request is still pending, yet its gate transaction is over:
    # two independent connections can compete for the remaining global slot.
    outcomes = race({'left': (users[1], ids[1], requests[1]),
                     'right': (users[2], ids[2], requests[2])}, 'global_capacity_one_winner')
    check(any(v is True or v == 'agent_global_capacity' for v in outcomes),
          'gate_released', outcomes, ['created'], users[0].id)
    check(outcomes.count(True) == 1 and outcomes.count('agent_global_capacity') == 1,
          'global_capacity_one_winner', outcomes, ['created', 'agent_global_capacity'], users[0].id)
    finish(users[0], ids[0], requests[0])
    for i, race_outcome in zip((1, 2), outcomes):
        if race_outcome is True:
            finish(users[i], ids[i], requests[i])

    users, ids = scenario_users(1)
    rid = uuid4().hex
    outcomes = race({'left': (users[0], ids[0], rid), 'right': (users[0], ids[0], rid)},
                    'duplicate_idempotency')
    check(outcomes.count(False) == 1 and outcomes.count(True) == 1, 'duplicate_idempotency',
          outcomes, ['duplicate', 'created'], users[0].id)
    finish(users[0], ids[0], rid)

    users, ids = scenario_users(1)
    # Three recent accepted turns including failed attempts consume the minute
    # allowance. Race the last available slot after two committed completions.
    for _ in range(2):
        rid = uuid4().hex
        value = attempt(users[0], ids[0], rid)[1]
        check(value is True, 'minute_initial_allowed', [value], ['created'], users[0].id)
        finish(users[0], ids[0], rid)
    requests = [uuid4().hex, uuid4().hex]
    outcomes = race({'left': (users[0], ids[0], requests[0]),
                     'right': (users[0], ids[0], requests[1])}, 'minute_window_one_winner')
    check(outcomes.count(True) == 1, 'minute_window_one_winner', outcomes, ['created'], users[0].id)
    # The competing request may see the pending owner first; after finishing,
    # the same rejected ID must be refused by the rolling frequency limit.
    winner = outcomes.index(True)
    finish(users[0], ids[0], requests[winner])
    value = attempt(users[0], ids[0], requests[1-winner])[1]
    check(value == 'agent_rate_limited', 'minute_window_retry', [value], ['agent_rate_limited'], users[0].id)

    # Synthetic old accepted turns use database UTC, not Worker wall clocks.
    # They are all outside the minute window and inside the hour window.
    users, ids = scenario_users(1)
    with Session(engine) as session, session.begin():
        now = admission.database_utc(session)
        session.get(Conversation, ids[0]).next_turn_no = 21
        for i in range(20):
            stamp = now - timedelta(seconds=121 + i * 61)
            status = ('pending', 'completed', 'failed', 'interrupted')[i % 4]
            row = ChatTurn(conversation_id=ids[0], turn_no=i + 1, client_request_id=uuid4().hex,
                           request_digest=digest, status=status, created_at=stamp)
            session.add(row)
            session.flush()
            session.add(ChatMessage(turn_id=row.id, position=0, role='user', content='question', created_at=stamp))
            if status == 'completed':
                session.add(ChatMessage(turn_id=row.id, position=1, role='assistant', content='synthetic answer',
                                        created_at=stamp))
    value = attempt(users[0], ids[0], uuid4().hex)[1]
    check(value == 'agent_rate_limited', 'hour_window_initial_limit', [value], ['agent_rate_limited'], users[0].id)
    with Session(engine) as session, session.begin():
        oldest = session.scalar(sa.select(ChatTurn).where(ChatTurn.conversation_id == ids[0])
                                .order_by(ChatTurn.created_at).limit(1))
        oldest.created_at = admission.database_utc(session) - timedelta(seconds=3600)
    requests = [uuid4().hex, uuid4().hex]
    outcomes = race({'left': (users[0], ids[0], requests[0]),
                     'right': (users[0], ids[0], requests[1])}, 'hour_window_one_winner')
    check(outcomes.count(True) == 1, 'hour_window_one_winner', outcomes, ['created'], users[0].id)
    winner = outcomes.index(True)
    finish(users[0], ids[0], requests[winner])
    value = attempt(users[0], ids[0], requests[1-winner])[1]
    check(value == 'agent_rate_limited', 'hour_window_retry', [value], ['agent_rate_limited'], users[0].id)

    users, ids = scenario_users(1)
    rid = uuid4().hex
    try:
        attempt(users[0], ids[0], rid, rollback=True)
    except RuntimeError as error:
        check(str(error) == 'synthetic_transaction_rollback', 'rollback_observed',
              [str(error) == 'synthetic_transaction_rollback'], ['created'], users[0].id)
    else:
        check(False, 'rollback_observed', [False], ['created'], users[0].id)
    with application.test_request_context():
        login_user(users[0])
        absent = c.get_request(ids[0], rid) is None
        check(absent, 'rollback_no_turn', [absent], ['created'], users[0].id)
        released = c.get_conversation(ids[0])['active_request_id'] is None
        check(released, 'rollback_no_active', [released], ['created'], users[0].id)
    value = attempt(users[0], ids[0], rid)[1]
    check(value is True, 'rollback_retry_allowed', [value], ['created'], users[0].id)
    finish(users[0], ids[0], rid)
    check(len(physical) >= 2, 'independent_connections', [len(physical) >= 2], ['created'])
    return dict.fromkeys(('same_user', 'global_slot', 'duplicate', 'rate', 'rollback',
                          'gate_released', 'independent_connections'), True)
