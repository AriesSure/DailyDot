"""Agent-only HTTP policy using public HTTPX2/SDK interfaces, never legacy fallback."""
from contextlib import contextmanager
import json
import os
import ssl
import time
from threading import Lock

import certifi
import httpx2
from openai import OpenAI

from app.agent.tools import AgentError

MODEL = 'deepseek-flash'
BASE = 'https://api.deepseek.com'
MAX_SENDS = 6
RUN_SECONDS = 90
PHASE_SECONDS = 20
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class TransportError(AgentError):
    """Fixed codes only; no raw network exception is a public diagnostic."""


class Budget:
    def __init__(self, *, clock=None):
        self.clock = clock or time.monotonic
        self.deadline = self.clock() + RUN_SECONDS
        self.sends = 0
        self.lock = Lock()

    def remaining(self):
        remaining = self.deadline - self.clock()
        if remaining <= 0:
            raise TransportError('agent_deadline_exceeded')
        return remaining

    def charge(self):
        with self.lock:
            remaining = self.remaining()
            if self.sends >= MAX_SENDS:
                raise TransportError('agent_http_budget_exhausted')
            self.sends += 1
            return min(PHASE_SECONDS, remaining)


def configuration(environ):
    key = environ.get('LLM_API_KEY', '')
    if not isinstance(key, str) or not key.strip():
        raise TransportError('agent_model_unavailable')
    if ('\r' in key or '\n' in key or environ.get('LLM_BASE_URL', BASE) not in (BASE, BASE + '/')
            or environ.get('LLM_MODEL', MODEL) != MODEL):
        raise TransportError('agent_transport_config_invalid')
    return key


class GuardedTransport(httpx2.BaseTransport):
    def __init__(self, budget, key, *, backend=None):
        self.budget = budget
        self.authorization = 'Bearer ' + key
        self.closed = False
        # Explicit CA file avoids SSL_CERT_FILE/SSL_CERT_DIR environment overrides.
        context = ssl.create_default_context(cafile=certifi.where())
        self.backend = backend if backend is not None else httpx2.HTTPTransport(
            verify=context, trust_env=False, retries=0)

    def handle_request(self, request):
        if self.closed:
            raise TransportError('agent_http_failed')
        url = request.url
        if (request.method != 'POST' or url.scheme != 'https' or url.host != 'api.deepseek.com'
                or url.port not in (None, 443) or url.username or url.password or url.query or url.fragment
                or url.raw_path != b'/chat/completions'
                or request.headers.get('host') not in ('api.deepseek.com', 'api.deepseek.com:443')
                or request.headers.get('authorization') != self.authorization):
            raise TransportError('agent_target_denied')
        if request.headers.get('x-stainless-retry-count', '0') != '0':
            raise TransportError('agent_http_failed')
        try:
            body = json.loads(request.content)
        except (ValueError, UnicodeError):
            raise TransportError('agent_target_denied') from None
        if type(body) is not dict or body.get('model') != MODEL or body.get('stream') not in (None, False):
            raise TransportError('agent_target_denied')
        timeout = self.budget.charge()  # Failures count, before handing bytes to the backend.
        request.extensions['timeout'] = dict.fromkeys(('connect', 'read', 'write', 'pool'), timeout)
        request.headers['Accept-Encoding'] = 'identity'
        response = None
        try:
            response = self.backend.handle_request(request)
            self.budget.remaining()
            if 300 <= response.status_code < 400:
                raise TransportError('agent_redirect_denied')
            if response.headers.get('content-encoding', 'identity').lower() != 'identity':
                raise TransportError('agent_response_limit')
            data, size = [], 0
            for chunk in response.iter_bytes():
                self.budget.remaining()
                size += len(chunk)
                if size > MAX_RESPONSE_BYTES:
                    raise TransportError('agent_response_limit')
                data.append(chunk)
            self.budget.remaining()
            return httpx2.Response(response.status_code, headers=response.headers,
                                   content=b''.join(data), request=request)
        except TransportError:
            raise
        except Exception:
            raise TransportError('agent_http_failed') from None
        finally:
            if response is not None:
                response.close()

    def close(self):
        if not self.closed:
            self.closed = True
            self.backend.close()


def error_code(exc):
    """SDK may wrap a transport exception. Inspect typed causes, never messages."""
    for _ in range(5):
        if isinstance(exc, TransportError):
            return exc.args[0]
        exc = exc.__cause__
        if exc is None:
            break
    return 'agent_http_failed'


@contextmanager
def agent_client(*, budget=None):
    """A new client and budget for each Agent run, shared across model decisions."""
    key = configuration(os.environ)  # Before any credential-bearing client exists.
    budget = budget if budget is not None else Budget()
    transport = GuardedTransport(budget, key)
    with httpx2.Client(transport=transport, trust_env=False, follow_redirects=False,
                       timeout=PHASE_SECONDS, headers={'Accept-Encoding': 'identity'}) as http:
        with OpenAI(api_key=key, base_url=BASE, http_client=http,
                    max_retries=0, timeout=PHASE_SECONDS) as client:
            yield client, budget
