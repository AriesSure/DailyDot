"""Explicit paid smoke runner. Import and default CLI never perform requests.

Only the narrow DeepSeek profile below is supported; no arbitrary URL/model.
This Python boundary assumes trusted installed code, not a hostile process.
"""
import argparse
from contextlib import redirect_stdout, redirect_stderr
from datetime import date, timedelta
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
REASONS = ('stop', 'tool_calls', 'length', 'content_filter', 'unknown')


class SmokeError(RuntimeError):
    pass


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
    from flask_login import login_user
    from app.factory import create_app
    from app.extensions import db
    from app.models import User, Habit, Record
    from app.agent import service
    from app.ai.llm_client import get_llm
    from tests.database_lifecycle import isolated_database

    traces = []
    app = create_app('testing')
    with isolated_database(app, db):
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
        llm = get_llm()
        if llm.base_url != base or llm.model != MODEL or llm.api_key != os.environ['LLM_API_KEY']:
            raise SmokeError('configuration_changed')
        with sdk_client(os.environ['LLM_API_KEY'], base, budget, [h.id for h in habits]) as sdk:
            resource_class = type(sdk.chat.completions)
            original_create, original_query = resource_class.create, service.query_tool
            current = None

            def observe_create(resource, *args, **kwargs):
                response = original_create(resource, *args, **kwargs)
                reason = response.choices[0].finish_reason
                current['finish_reasons'].append(reason if reason in REASONS else 'unknown')
                return response

            def observe_query(user_id, name, args):
                if user_id != a.id or name not in ('list_user_habits', 'get_checkin_summary'):
                    raise SmokeError('foreign_identity_blocked')
                try:
                    value = original_query(user_id, name, args)
                except Exception:
                    current['tool_error'] = True
                    raise
                current['tools'].append(name)
                return value

            with patch.object(llm, '_client', sdk), patch.object(resource_class, 'create', observe_create), \
                    patch.object(service, 'query_tool', observe_query):
                for scene, question, required in SCENARIOS:
                    budget.start(scene)
                    current = {'scenario': scene, 'model': MODEL, 'tools': [],
                               'finish_reasons': [], 'tool_error': False}
                    with app.test_request_context():
                        login_user(a)
                        result = service.answer(question)
                    current.update(requests=budget.counts[scene], tool_count=len(current['tools']),
                                   ended_normally=result['ok'],
                                   limit_hit=budget.limited or result['error_code'] in (
                                       'agent_tool_limit', 'agent_step_limit',
                                       'agent_context_limit', 'agent_result_limit'),
                                   required_tools_used=required <= set(current['tools']),
                                   answer_consistency='unverified')
                    traces.append(current)
                    if not result['ok']:
                        break  # No scenario retry, fallback provider or automatic repair.
    return {'mode': 'offline_test' if offline else 'manual_online',
            'status': ('content_review_required' if len(traces) == 3 and all(
                t['ended_normally'] and t['required_tools_used'] for t in traces) else 'failed'),
            'online_accepted': False,
            'requests': budget.total, 'scenarios': traces}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Manual paid Agent smoke; never run by pytest/CI.')
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
        # Reviewed sending path. Unknown installed implementations fail closed.
        if any(version(name) != expected for name, expected in
               (('openai', '3.45.0'), ('httpx', '0.28.1'), ('httpcore', '1.0.9'))):
            raise SmokeError('transport_version_unreviewed')
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
                 'explicit_confirmation_required', 'transport_version_unreviewed', 'target_approval_required'}
        result['error_code'] = str(exc) if type(exc) is SmokeError and str(exc) in codes else 'smoke_failed'
        result['requests'] = budget.total
        if budget.total:
            result['status'] = 'failed'
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result.get('status') == 'content_review_required' else 2


if __name__ == '__main__':
    raise SystemExit(main())
