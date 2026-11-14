"""Explicit paid smoke runner. Import and default CLI never perform requests.

Only the narrow DeepSeek profile below is supported; no arbitrary URL/model.
This Python boundary assumes trusted installed code, not a hostile process.
"""
import argparse
from contextlib import redirect_stdout, redirect_stderr
from datetime import date, timedelta, datetime, timezone
from uuid import uuid4
from importlib.metadata import version
import io
import json
import logging
import os
from pathlib import Path
import sys
from threading import Lock
from unittest.mock import patch

import httpx


ROOT = Path(__file__).resolve().parents[1]
MODEL = 'deepseek-flash'
BASES = ('https://api.deepseek.com',)
SCENARIOS = (
    ('S1', '我目前有哪些习惯？', {'list_user_habits'}),
    ('S2', '我最近七天的打卡情况怎么样？', {'get_checkin_summary'}),
    ('S3', '结合我最近七天的打卡情况和现有习惯，分析一下我有哪些需要改善的地方。',
     {'list_user_habits', 'get_checkin_summary'}),
)
STAGES = ('configuration', 'dependencies', 'imports', 'application_init', 'database_init',
          'demo_data', 'llm_configuration', 'sdk_init', 'patch_install', 'agent_execution',
          'http_validation', 'http_send')

REASONS = ('stop', 'tool_calls', 'length', 'content_filter', 'unknown')


class SmokeError(RuntimeError):
    pass


MAX_EVIDENCE_BYTES = 262144


class LocalEvidence:
    """Synthetic business evidence only; never a serializer for SDK objects."""
    def __init__(self, environ, *, offline=False):
        base = environ.get('LOCALAPPDATA')
        if not base or not Path(base).is_absolute() or base.startswith(('\\\\', '//')):
            raise SmokeError('evidence_location_invalid')
        self.directory = Path(base) / 'DailyDot' / 'agent-smoke-evidence'
        self.run_id = uuid4().hex
        self.key = environ.get('LLM_API_KEY', '')
        self.document = {'run_id': self.run_id, 'created_at': datetime.now(timezone.utc).isoformat(),
                         'mode': 'offline_test' if offline else 'manual_online', 'model': MODEL, 'scenarios': []}
        self.status = {'raw': 'not_created', 'review': 'not_created', 'run_id': self.run_id}
        self.user_id = None
        self.facts = []
        self.dates = []
        self.current = None

    def bind(self, user_id, facts, dates):
        self.user_id = user_id
        self.facts = json.loads(json.dumps(facts))
        self.dates = list(dates)

    def identity(self, user_id):
        if user_id != self.user_id or self.user_id is None:
            raise SmokeError('evidence_identity_invalid')

    def start(self, scenario, question):
        self.current = {'scenario_id': scenario, 'user_question': question, 'tool_calls': [],
                        'tool_results': [], 'final_answer': None, 'answer_status': 'incomplete',
                        'synthetic_data_created_at': self.document.get('synthetic_data_created_at'),
                        'expected_facts': [{**h, 'expected_seven_day_checkins': sum(
                            hid == h['id'] and date.today()-timedelta(days=6) <= day <= date.today()
                            for hid, day in self.dates)} for h in self.facts],
                        'expected_date_range': {
                            'start_date': (date.today()-timedelta(days=6)).isoformat(),
                            'end_date': date.today().isoformat()},
                        'expected_metric_semantics': 'checkin_count_not_completion_rate',
                        'actual_date_ranges': [], 'consistency_status': 'pending'}
        self.document['scenarios'].append(self.current)

    def calls(self, calls):
        from app.agent.tools import validate
        for call in calls or []:
            name, cid = call.function.name, call.id
            if name not in ('list_user_habits', 'get_checkin_summary'):
                raise SmokeError('evidence_tool_invalid')
            if any(c['tool_call_id'] == cid for c in self.current['tool_calls']):
                raise SmokeError('evidence_call_invalid')
            args = json.loads(call.function.arguments)
            validate(name, args)
            self.current['tool_calls'].append({'tool_call_id': cid, 'tool_name': name, 'arguments': args,
                                               'validation_status': 'valid', 'execution_status': 'not_executed'})

    def result(self, user_id, name, args, value=None, *, failed=False):
        """FIFO correlation ONLY for service.build_graph's synchronous tools loop.

        query_tool supplies no call ID: identical arguments are correlated by
        execution order, not independently authenticated IDs. This collector
        must not be shared across concurrent graph invocations or tool workers.
        Re-audit before introducing ToolNode/Send/async or parallel execution.
        """
        self.identity(user_id)
        call = next((c for c in self.current['tool_calls'] if c['execution_status'] == 'not_executed'), None)
        if call is None or call['tool_name'] != name or call['arguments'] != args:
            raise SmokeError('evidence_call_invalid')
        if failed:
            call['execution_status'] = 'failed'
            return
        allowed = {'habits'} if name == 'list_user_habits' else {
            'habits', 'start_date', 'end_date', 'metric'}
        row_fields = {'id', 'name', 'frequency', 'note'} if name == 'list_user_habits' else {
            'id', 'name', 'checkins'}
        if type(value) is not dict or set(value) != allowed or type(value['habits']) is not list:
            raise SmokeError('evidence_result_invalid')
        owned = {h['id'] for h in self.facts}
        if any(type(row) is not dict or set(row) != row_fields or row['id'] not in owned
               for row in value['habits']):
            raise SmokeError('evidence_foreign_data')
        clean = json.loads(json.dumps(value))
        self.current['tool_results'].append({'tool_call_id': call['tool_call_id'], 'tool_name': name,
                                             'result': clean})
        if name == 'get_checkin_summary':
            start, end = date.fromisoformat(value['start_date']), date.fromisoformat(value['end_date'])
            counts = {str(h['id']): sum(hid == h['id'] and start <= day <= end for hid, day in self.dates)
                      for h in self.facts}
            self.current['actual_date_ranges'].append({'tool_call_id': call['tool_call_id'],
                                                       'start_date': start.isoformat(), 'end_date': end.isoformat(),
                                                       'independent_expected_counts': counts})
        call['execution_status'] = 'succeeded'

    def finish(self, user_id, result):
        self.identity(user_id)
        text = result.get('answer')
        if result.get('ok') is True and type(text) is str and text.strip():
            self.current['final_answer'] = text
            self.current['answer_status'] = 'available'

    def save(self):
        status = {'raw': 'failed', 'review': 'not_created', 'run_id': self.run_id}
        try:
            complete = len(self.document['scenarios']) == 3 and all(
                s['answer_status'] == 'available' and s['tool_results'] and all(
                    c['execution_status'] == 'succeeded' for c in s['tool_calls'])
                for s in self.document['scenarios'])
            self.document['evidence_status'] = 'complete' if complete else 'incomplete'
            encoded = json.dumps(self.document, ensure_ascii=True, allow_nan=False).encode('utf-8')
            tokens = ('MVP15_B_ONLY', 'Authorization', 'Bearer ', 'mysql://', 'mysql+pymysql://',
                      'sqlite:', 'api_key', 'password', 'Cookie', 'Session')

            def sensitive(value):
                if isinstance(value, str):
                    return bool(self.key and self.key in value) or any(token in value for token in tokens)
                if isinstance(value, dict):
                    return any(sensitive(k) or sensitive(v) for k, v in value.items())
                if isinstance(value, list):
                    return any(sensitive(v) for v in value)
                return False

            if sensitive(self.document):
                raise SmokeError('evidence_sensitive_data')
            if len(encoded) > MAX_EVIDENCE_BYTES:
                raise SmokeError('evidence_size_limit')
            for parent in (self.directory, *self.directory.parents):
                reparse = parent.exists() and getattr(parent.lstat(), 'st_file_attributes', 0) & 0x400
                if parent.is_symlink() or reparse:
                    raise SmokeError('evidence_location_invalid')
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            raw = self.directory / (self.run_id + '.evidence.json')
            with raw.open('xb') as stream:
                stream.write(encoded)
            raw.chmod(0o400)  # Application never reopens raw evidence for modification.
            status['raw'] = 'saved'
            review = {'run_id': self.run_id, 'consistency_status': 'pending', 'scenarios': [
                {'scenario_id': s['scenario_id'], 'reviewed_at': None, 'consistency_status': 'pending',
                 'reviewed_claims': [], 'supporting_tool_call_ids': [], 'contradicted_claims': [],
                 'unsupported_claims': [], 'review_notes': ''} for s in self.document['scenarios']]}
            with (self.directory / (self.run_id + '.review.json')).open('x', encoding='utf-8') as stream:
                json.dump(review, stream, ensure_ascii=True)
            status['review'] = 'saved'
        except Exception as exc:
            safe_codes = {
                'evidence_sensitive_data', 'evidence_size_limit', 'evidence_location_invalid'}
            status['error_code'] = (str(exc) if type(exc) is SmokeError and str(exc) in safe_codes
                                    else 'evidence_write_failed')
            if status['raw'] == 'saved':
                status['review'] = 'failed'
        return status


def configuration(env):
    if not env.get('LLM_API_KEY', '').strip():
        raise SmokeError('key_missing')
    if env.get('LLM_BASE_URL') not in BASES:
        raise SmokeError('target_not_approved')
    if env.get('LLM_MODEL') != MODEL:
        raise SmokeError('model_not_approved')
    return env['LLM_BASE_URL'], env['LLM_MODEL']


class Budget:
    def __init__(self):
        self.evidence = None
        self.stage = 'configuration'
        self.total = 0
        self.scene = None
        self.counts = {}
        self.lock = Lock()
        self.limited = False

    def start(self, scene):
        with self.lock:
            if scene not in ('S1', 'S2', 'S3') or scene in self.counts:
                raise SmokeError('scene_invalid')
            self.scene = scene
            self.counts[scene] = 0

    def charge(self):
        with self.lock:
            if self.scene is None or self.total >= 12 or self.counts[self.scene] >= 4:
                self.limited = True
                raise SmokeError('request_budget_exhausted')
            self.total += 1
            self.counts[self.scene] += 1


class GuardedTransport(httpx.HTTPTransport):
    def __init__(self, budget, owned_ids):
        super().__init__(verify=True, trust_env=False, retries=0, http2=False)
        self.budget, self.owned_ids = budget, frozenset(owned_ids)

    def handle_request(self, request):
        self.budget.stage = 'http_validation'
        url = request.url
        if (request.method != 'POST' or url.scheme != 'https' or url.host != 'api.deepseek.com'
                or url.port not in (None, 443) or url.username or url.password or url.query
                or url.fragment or url.path != '/chat/completions'):
            raise SmokeError('request_target_invalid')
        body = json.loads(request.content)
        if body.get('model') != MODEL or body.get('stream') not in (None, False):
            raise SmokeError('request_body_invalid')
        # Tool rows must belong to A BEFORE bytes reach the HTTP backend.
        if b'MVP15_B_ONLY' in request.content:
            raise SmokeError('foreign_data_blocked')
        for message in body['messages']:
            if message['role'] == 'tool':
                data = json.loads(message['content'])
                if any(row['id'] not in self.owned_ids for row in data['habits']):
                    raise SmokeError('foreign_data_blocked')
        self.budget.charge()  # Failed sends consume quota too.
        self.budget.stage = 'http_send'
        response = super().handle_request(request)
        if 300 <= response.status_code < 400:
            response.close()
            raise SmokeError('redirect_rejected')
        return response


def sdk_client(key, base, budget, owned_ids):
    from openai import OpenAI
    transport = GuardedTransport(budget, owned_ids)
    client = httpx.Client(transport=transport, follow_redirects=False, trust_env=False, timeout=20)
    return OpenAI(api_key=key, base_url=base, max_retries=0, timeout=20, http_client=client)


def run_suite(base, budget, *, offline=False):
    """CLI uses real ConfiguredModel only. offline labels synthetic transport tests."""
    budget.stage = 'imports'
    from flask_login import login_user
    from app.factory import create_app
    from app.extensions import db
    from app.models import User, Habit, Record
    from app.agent import service
    from app.ai.llm_client import get_llm
    from tests.database_lifecycle import isolated_database

    traces = []
    budget.stage = 'application_init'
    app = create_app('testing')
    budget.stage = 'database_init'
    with isolated_database(app, db):
        budget.stage = 'demo_data'
        created_date = date.today()
        a = User(username='mvp15-a', email='a@example.invalid')
        b = User(username='mvp15-b', email='b@example.invalid')
        db.session.add_all([a, b])
        db.session.flush()
        habits = [Habit(user_id=a.id, habit_name=name, icon='book', frequency=frequency)
                  for name, frequency in (('每日阅读', 'Every day this week'),
                                          ('每周运动', 'Once a week'), ('每日英语', 'Every day this week'))]
        other = Habit(user_id=b.id, habit_name='MVP15_B_ONLY', icon='book')
        db.session.add_all(habits + [other])
        db.session.flush()
        for habit, count in zip(habits, (3, 1, 2)):
            for offset in range(count):
                db.session.add(Record(habit_id=habit.id, checkin_date=date.today()-timedelta(days=offset)))
        db.session.add_all([Record(habit_id=habits[0].id, checkin_date=date.today()-timedelta(days=15)),
                            Record(habit_id=other.id, checkin_date=date.today())])
        db.session.commit()
        evidence = budget.evidence
        if evidence is not None:
            evidence.bind(a.id, [{'id': h.id, 'name': h.habit_name, 'frequency': h.frequency,
                                  'note': h.note or ''} for h in habits],
                          [(r.habit_id, r.checkin_date) for r in db.session.query(Record).join(Habit).filter(
                              Habit.user_id == a.id).all()])
            evidence.document['synthetic_data_created_at'] = created_date.isoformat()
        budget.stage = 'llm_configuration'
        llm = get_llm()
        if llm.base_url != base or llm.model != MODEL or llm.api_key != os.environ['LLM_API_KEY']:
            raise SmokeError('configuration_changed')
        budget.stage = 'sdk_init'
        with sdk_client(os.environ['LLM_API_KEY'], base, budget, [h.id for h in habits]) as sdk:
            budget.stage = 'patch_install'
            resource_class = type(sdk.chat.completions)
            original_create, original_query = resource_class.create, service.query_tool
            current = None

            def observe_create(resource, *args, **kwargs):
                response = original_create(resource, *args, **kwargs)
                if evidence is not None:
                    evidence.calls(response.choices[0].message.tool_calls)
                reason = response.choices[0].finish_reason
                current['finish_reasons'].append(reason if reason in REASONS else 'unknown')
                return response

            def observe_query(user_id, name, args):
                if user_id != a.id or name not in ('list_user_habits', 'get_checkin_summary'):
                    raise SmokeError('foreign_identity_blocked')
                try:
                    value = original_query(user_id, name, args)
                except Exception:
                    if evidence is not None:
                        evidence.result(user_id, name, args, failed=True)
                    current['tool_error'] = True
                    raise
                if evidence is not None:
                    evidence.result(user_id, name, args, value)
                current['tools'].append(name)
                return value

            with patch.object(llm, '_client', sdk), patch.object(resource_class, 'create', observe_create), \
                    patch.object(service, 'query_tool', observe_query):
                for scene, question, required in SCENARIOS:
                    budget.start(scene)
                    if evidence is not None:
                        evidence.start(scene, question)
                    current = {'scenario': scene, 'model': MODEL, 'tools': [],
                               'finish_reasons': [], 'tool_error': False}
                    with app.test_request_context():
                        login_user(a)
                        budget.stage = 'agent_execution'
                        result = service.answer(question)
                        if evidence is not None:
                            from flask_login import current_user
                            evidence.finish(int(current_user.id), result)
                    current.update(requests=budget.counts[scene], tool_count=len(current['tools']),
                                   ended_normally=result['ok'],
                                   limit_hit=budget.limited or result['error_code'] in (
                                       'agent_tool_limit', 'agent_step_limit',
                                       'agent_context_limit', 'agent_result_limit'),
                                   required_tools_used=required <= set(current['tools']),
                                   answer_consistency='unverified')
                    if not result['ok']:
                        stage = budget.stage if budget.stage in STAGES else 'unknown'
                        current.update(stage=stage, error_code='smoke_' + stage + '_failed')
                    traces.append(current)
                    if not result['ok']:
                        break  # No scenario retry, fallback provider or automatic repair.
        if evidence is not None:
            with app.test_request_context():
                login_user(a)
                from flask_login import current_user
                evidence.identity(int(current_user.id))
                evidence.status = evidence.save()
    return {'mode': 'offline_test' if offline else 'manual_online',
            'status': ('content_review_required' if len(traces) == 3 and all(
                t['ended_normally'] and t['required_tools_used'] for t in traces) else 'failed'),
            'online_accepted': False,
            'requests': budget.total, 'scenarios': traces}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Manual paid Agent smoke; never run by pytest/CI.')
    parser.add_argument('--save-local-evidence', action='store_true',
                        help='Save synthetic content and pending review under LOCALAPPDATA; no extra model calls.')
    parser.add_argument('--execute-paid-smoke', action='store_true')
    parser.add_argument('--approved-profile', choices=['deepseek-official'])
    args = parser.parse_args(argv)
    result = {'status': 'blocked', 'requests': 0, 'online_accepted': False}
    budget = Budget()
    try:
        if not args.execute_paid_smoke:
            raise SmokeError('explicit_confirmation_required')
        if args.approved_profile != 'deepseek-official':
            raise SmokeError('target_approval_required')
        base, _ = configuration(os.environ)
        budget.stage = 'dependencies'
        # Reviewed sending path. Unknown installed implementations fail closed.
        if any(version(name) != expected for name, expected in
               (('openai', '3.45.0'), ('httpx', '0.28.1'), ('httpcore', '1.0.9'))):
            raise SmokeError('transport_version_unreviewed')
        if args.save_local_evidence:
            budget.evidence = LocalEvidence(os.environ)
        os.environ.update(FLASK_ENV='testing', TEST_DATABASE_PROFILE='sqlite-memory',
                          TEST_DATABASE_URL='sqlite:///:memory:', TEST_DATABASE_ALLOW_MYSQL='')
        os.environ.pop('DATABASE_URL', None)
        os.environ['SECRET_KEY'] = 'synthetic-smoke-session-only'
        sys.path.insert(0, str(ROOT))
        old_logging = logging.root.manager.disable
        try:
            logging.disable(logging.CRITICAL)  # Dedicated process: no raw SDK/debug log export.
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                result = run_suite(base, budget)
        finally:
            logging.disable(old_logging)
    except Exception as exc:
        codes = {'key_missing', 'target_not_approved', 'model_not_approved',
                 'explicit_confirmation_required', 'transport_version_unreviewed', 'target_approval_required',
                 'configuration_changed', 'evidence_location_invalid'}
        fallback = 'smoke_' + budget.stage + '_failed' if budget.stage in STAGES else 'smoke_failed'
        result['error_code'] = str(exc) if type(exc) is SmokeError and str(exc) in codes else fallback
        result['stage'] = budget.stage if budget.stage in STAGES else 'unknown'
        types = {'ModuleNotFoundError', 'ImportError', 'TypeError', 'AttributeError', 'RuntimeError', 'SmokeError'}
        name = type(exc).__name__
        result['exception_type'] = name if name in types else 'OtherException'
        result['requests'] = budget.total
        if budget.total:
            result['status'] = 'failed'
    if budget.evidence is not None:
        result['evidence'] = budget.evidence.status
        if result['evidence']['raw'] != 'saved' or result['evidence']['review'] != 'saved':
            result['status'] = 'failed'
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result.get('status') == 'content_review_required' else 2


if __name__ == '__main__':
    raise SystemExit(main())
