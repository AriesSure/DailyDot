"""Check the actual HTML and JSON produced for untrusted AI and stored text."""

import json
from html.parser import HTMLParser
from types import SimpleNamespace

import pytest

from app.extensions import db
from app.models import Habit, User
from tests.test_http_method_csrf_security import browser, csrf_header, sign_in


class ReportHTML(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.in_report = False
        self.nodes = []
        self.text = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if self.in_report:
            self.nodes.append((tag, dict(attrs)))
        elif tag == 'div' and dict(attrs).get('id') == 'ai-report-content':
            self.in_report = True

    def handle_endtag(self, tag):
        if tag == 'div':
            self.in_report = False

    def handle_data(self, data):
        if self.in_report:
            self.text.append(data)


def report(client, monkeypatch, source):
    fake = SimpleNamespace(available=True, chat=lambda *args, **kwargs: source)
    monkeypatch.setattr('app.blueprints.ai.routes.get_llm', lambda: fake)
    response = client.get('/ai/report')
    assert response.status_code == 200
    return ReportHTML(response.get_data(as_text=True))


@pytest.mark.parametrize('payload', [
    '<script>alert(1)</script>',
    '<img src=x onerror=alert(1)>',
    '<svg onload=alert(1)><a href="javascript:alert(1)">svg</a></svg>',
    '<math><mtext><iframe srcdoc="<img src=x onerror=alert(1)>"></iframe></mtext></math>',
    '<p style="color:red" onclick="alert(1)">embedded</p>',
    '<object data="data:text/html,example"></object><embed src="example">',
    '&lt;img src=x onerror=alert(1)&gt;',
])
def test_report_renders_html_as_text(client, auth_headers, monkeypatch, payload):
    page = report(client, monkeypatch, '## 报告\n\n' + payload)
    assert page.nodes == [('h2', {}), ('p', {})]
    assert payload.replace('&lt;', '<').replace('&gt;', '>') in ''.join(page.text)


@pytest.mark.parametrize('destination', [
    'javascript:alert(1)', 'JaVaScRiPt&colon;alert(1)', 'java&#x09;script:alert(1)',
    '&#106;avascript:alert(1)', 'data:text/html,example', 'vbscript:alert(1)',
    '//example.test/image', '/account', 'https://user:pass@example.test',
    'https://example.test/&#x0a;bad', 'https://example.test\\@elsewhere.test',
])
def test_report_renders_unsafe_link_labels_without_href(client, auth_headers, monkeypatch, destination):
    page = report(client, monkeypatch, f'[阅读建议]({destination})')
    assert page.nodes == [('p', {}), ('span', {})]
    assert '阅读建议' in ''.join(page.text)


def test_report_keeps_markdown_structure_and_safe_links(client, auth_headers, monkeypatch):
    source = (
        '## 中文习惯报告\n\nA & B < C, **加粗**, *强调* and `<> &`.\n\n'
        '- 第一项\n- 第二项\n\n顺序\n\n1. 一\n2. 二\n\n> 引用\n\n'
        '[详细](https://example.test/a?x=1&y=2 "Quoted title") '
        '[reference][ref] <https://example.test/auto> '
        '[bad reference][bad] ![image](https://example.test/image)\n\n'
        '[ref]: http://example.test/reference\n[bad]: javascript:alert(1)\n\n'
        '| Habit | Count |\n| --- | ---: |\n| 阅读 | 2 |\n'
    )
    page = report(client, monkeypatch, source)
    tags = [tag for tag, _ in page.nodes]
    assert {'h2', 'p', 'strong', 'em', 'code', 'ul', 'ol', 'li', 'blockquote', 'table', 'td'} <= set(tags)
    assert 'A & B < C' in ''.join(page.text)
    assert [attrs for tag, attrs in page.nodes if tag == 'a'] == [
        {'href': url, 'rel': 'noopener noreferrer'} for url in (
            'https://example.test/a?x=1&y=2', 'http://example.test/reference', 'https://example.test/auto',
        )
    ]
    assert 'img' not in tags
    assert all(tag == 'a' or not attrs for tag, attrs in page.nodes)


def test_report_fences_keep_code_literal_and_discard_attributes(client, auth_headers, monkeypatch):
    code = '&lt;script&gt; <img src=x onerror=alert(1)>'
    source = (
        '~~~{#malicious .html onclick="alert(1)" style="color:red"}\n'
        f'{code}\n~~~\n\n    <svg onload=alert(1)>\n\n'
        '[link](https://example.test "x\n~~~html\n<img src=x onerror=alert(2)>\n~~~\ny")'
    )
    page = report(client, monkeypatch, source)
    assert [tag for tag, _ in page.nodes].count('code') == 3
    assert [tag for tag, _ in page.nodes].count('pre') == 3
    assert all(tag in {'p', 'pre', 'code'} and not attrs for tag, attrs in page.nodes)
    assert code in ''.join(page.text)
    assert '<svg onload=alert(1)>' in ''.join(page.text)


def test_report_fallback_renders_stored_habit_name_safely(client, auth_headers, monkeypatch):
    name = '阅读 <img src=x onerror=alert(1)>'
    response = client.post('/habits/new', json={
        'habit_name': name, 'icon': 'fas fa-book', 'frequency': 'Every day this week',
        'time_period': 'Morning', 'note': '<svg onload=alert(1)>',
    }, headers=auth_headers)
    assert response.json['success']
    monkeypatch.setattr('app.blueprints.ai.routes.get_llm', lambda: SimpleNamespace(available=False))
    response = client.get('/ai/report')
    assert response.status_code == 200
    page = ReportHTML(response.get_data(as_text=True))
    assert {'h2', 'ul', 'li'} <= {tag for tag, _ in page.nodes}
    assert name in ''.join(page.text)
    assert not any(tag == 'img' or attrs for tag, attrs in page.nodes)
    saved = db.session.query(Habit).one()
    assert (saved.habit_name, saved.note) == (name, '<svg onload=alert(1)>')


def test_home_embeds_stored_username_as_script_safe_json(client, auth_headers):
    name = r'\x3cimg src=x onerror=alert(1)\x3e </script> "&'
    user = db.session.query(User).one()
    user.username = name
    db.session.commit()
    response = client.get('/')
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assignment = next(line for line in html.splitlines() if 'const username = ' in line)
    literal = assignment.partition('=')[2].strip().removesuffix(';')
    assert json.loads(literal) == name
    assert '<' not in literal and '>' not in literal and '&' not in literal
    assert 'id="greetingText"' in html and 'src="/static/ai-habits.js"' in html
    asset = client.get('/static/ai-habits.js')
    assert asset.status_code == 200


def test_ai_json_preserves_model_fields_and_requires_real_csrf(sign_in, browser, monkeypatch):
    name = '阅读 <img src=x onerror=alert(1)>'
    note = '文字 " onclick="alert(1) &lt;script&gt;'
    habit = {'habit_name': name, 'note': note, 'frequency': 'Every day this week',
             'time_period': 'Morning', 'icon': 'fas fa-book'}
    suggestion = {'name': name, 'reason': note}
    fake = SimpleNamespace(
        available=True,
        chat=lambda *args, **kwargs: json.dumps([suggestion]),
        chat_with_tools=lambda *args, **kwargs: {'name': 'create_habit', 'arguments': habit},
    )
    monkeypatch.setattr('app.blueprints.ai.routes.get_llm', lambda: fake)
    monkeypatch.setattr('app.blueprints.ai.routes.HabitVectorStore', lambda: SimpleNamespace(
        search=lambda *args, **kwargs: [{
            'name': '阅读', 'frequency': 'Every day this week', 'time_period': 'Morning',
            'icon': 'fas fa-book', 'tags': ['learn'], 'score': 0.8,
        }],
    ))
    _, send, user_id, token = sign_in('xss-test-user')
    headers = csrf_header(token)
    goal = {'goal': '<svg onload=alert(1)> & 阅读'}
    assert send('POST', '/ai/recommend', json=goal).status_code == 400
    assert send('POST', '/ai/recommend', json=goal, headers=headers).json == {
        'success': True, 'source': 'llm', 'suggestions': [suggestion],
    }
    text = {'text': '<script>alert(1)</script> & 阅读'}
    assert send('POST', '/ai/parse-habit', json=text).status_code == 400
    assert send('POST', '/ai/parse-habit', json=text, headers=headers).json == {
        'success': True, 'habit': habit,
    }
    assert db.session.query(Habit).count() == 0
    assert send('POST', '/habits/new', json=habit).status_code == 400
    assert send('POST', '/habits/new', json=habit, headers=headers).json['success']
    saved = db.session.query(Habit).one()
    assert (saved.user_id, saved.habit_name, saved.note) == (user_id, name, note)

    _, anonymous = browser()
    assert '/auth/login' in anonymous('GET', '/ai/report').location
