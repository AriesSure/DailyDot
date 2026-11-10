"""Exercise the real Flask-WTF request boundary with isolated browser sessions."""

import json
import re
from datetime import date, timedelta
from unittest.mock import Mock

import pytest

from app.extensions import db
from app.models import Card, Habit, Record, Todo, User
from tests.test_card_image_security import PNG, data_url


CARD_SAVE = '/cards/api/card/save'
PASSWORD = 'Test-only@1234'
HABIT = {
    'habit_name': 'Daily reading',
    'icon': 'fas fa-book',
    'frequency': 'Every day this week',
    'time_period': 'Morning',
}


def csrf_token(page):
    assert page.status_code == 200
    match = re.search(r'<meta name="csrf-token" content="([^"]+)">', page.get_data(as_text=True))
    assert match is not None
    return match.group(1)


def csrf_header(token):
    return {'X-CSRFToken': token} if token is not None else {}


@pytest.fixture
def browser(app, monkeypatch, tmp_path):
    monkeypatch.setitem(app.config, 'WTF_CSRF_ENABLED', True)
    monkeypatch.setattr(app, 'static_folder', str(tmp_path.resolve()))

    def make_browser():
        client = app.test_client()

        def send(method, path, **kwargs):
            # The database fixture holds an app context. Give every request a
            # fresh one so Flask-Login cannot reuse another client's cached user.
            with app.app_context():
                return client.open(path, method=method, **kwargs)

        return client, send

    return make_browser


@pytest.fixture
def sign_in(browser):
    def login(name):
        client, send = browser()
        token = csrf_token(send('GET', '/auth/register'))
        assert send('POST', '/auth/register', data={
            'username': name, 'email': f'{name}@example.com',
            'password': PASSWORD, 'confirm': PASSWORD, 'csrf_token': token,
        }).status_code == 302
        assert send('POST', '/auth/login', data={
            'username': name, 'password': PASSWORD, 'remember_me': 'y',
            'csrf_token': token,
        }).status_code == 302
        user_id = db.session.query(User.id).filter_by(username=name).scalar()
        assert user_id is not None
        with client.session_transaction() as session:
            assert session['_user_id'] == str(user_id)
        page = send('GET', '/auth/account')
        assert f'{name}@example.com' in page.get_data(as_text=True)
        return client, send, user_id, csrf_token(page)

    return login


@pytest.fixture
def signed_in(sign_in):
    return sign_in('method-user-a')


def seed_habit(user_id, *, checked=False):
    habit = Habit(user_id=user_id, **HABIT)
    db.session.add(habit)
    if checked:
        db.session.flush()
        db.session.add(Record(habit_id=habit.id, checkin_date=date.today()))
    db.session.commit()
    return habit.id


def today_records(habit_id):
    return db.session.query(Record.id).filter_by(habit_id=habit_id, checkin_date=date.today()).count()


@pytest.mark.parametrize(('method', 'token_kind', 'status'), [
    ('GET', 'valid', 405),
    ('HEAD', 'valid', 405),
    ('POST', 'missing', 400),
    ('POST', 'invalid', 400),
    ('POST', 'valid', 302),
])
def test_method_uncheck_requires_post_and_csrf(signed_in, method, token_kind, status):
    _, send, user_id, token = signed_in
    habit_id = seed_habit(user_id, checked=True)
    form = {} if token_kind == 'missing' else {
        'csrf_token': token if token_kind == 'valid' else 'invalid-token',
    }
    response = send(method, f'/habits/uncheck/{habit_id}', data=form)
    assert response.status_code == status
    assert today_records(habit_id) == (0 if status == 302 else 1)


def test_method_uncheck_is_owner_scoped_and_safe_to_repeat(sign_in):
    _, send, user_id, token = sign_in('method-user-a')
    _, _, other_id, _ = sign_in('method-user-b')
    owned = seed_habit(user_id, checked=True)
    other = seed_habit(other_id, checked=True)
    for habit_id, remaining in ((other, 1), (9999, 0), (owned, 0), (owned, 0)):
        response = send('POST', f'/habits/uncheck/{habit_id}', data={'csrf_token': token})
        assert response.status_code == 302 and response.location == '/'
        assert today_records(habit_id) == remaining
    assert today_records(other) == 1


def test_method_calendar_uncheck_still_accepts_tokenized_json_post(signed_in):
    _, send, user_id, token = signed_in
    habit_id = seed_habit(user_id, checked=True)
    response = send('POST', f'/habits/uncheck_by_date/{habit_id}',
                    headers=csrf_header(token), json={'date': date.today().isoformat()})
    assert response.status_code == 200 and response.json['success']
    assert today_records(habit_id) == 0


@pytest.mark.parametrize(('method', 'token_kind', 'status'), [
    ('GET', 'valid', 405),
    ('HEAD', 'valid', 405),
    ('POST', 'missing', 400),
    ('POST', 'invalid', 400),
    ('POST', 'valid', 302),
])
def test_method_logout_requires_post_and_csrf(signed_in, method, token_kind, status):
    client, send, user_id, token = signed_in
    remember = client.get_cookie('remember_token')
    assert remember is not None
    form = {} if token_kind == 'missing' else {
        'csrf_token': token if token_kind == 'valid' else 'invalid-token',
    }
    response = send(method, '/auth/logout', data=form)
    assert response.status_code == status
    with client.session_transaction() as session:
        assert session.get('_user_id') == (None if status == 302 else str(user_id))
    if status == 302:
        assert response.location == '/'
        assert client.get_cookie('remember_token') is None
        assert '/auth/login' in send('GET', '/auth/account').location
    else:
        assert client.get_cookie('remember_token') == remember
        assert send('GET', '/auth/account').status_code == 200


def test_method_logout_uses_rendered_form_and_accepts_anonymous_post(browser, signed_in):
    _, send, _, token = signed_in
    html = send('GET', '/auth/account').get_data(as_text=True)
    form = re.search(r'<form method="post" action="/auth/logout"[^>]*>(.*?)</form>', html, re.S)
    assert form is not None
    assert f'name="csrf_token" value="{token}"' in form[1]
    assert '<button type="submit"' in form[1]
    assert '<button type="button" id="cancelLogout"' in form[1]

    _, anonymous = browser()
    token = csrf_token(anonymous('GET', '/auth/login'))
    assert anonymous('POST', '/auth/logout', data={'csrf_token': token}).location == '/'
    assert '/auth/login' in anonymous('GET', '/auth/account').location


@pytest.fixture(params=['habit', 'checkin', 'todo'])
def write_form(request, signed_in):
    _, _, user_id, _ = signed_in
    if request.param == 'habit':
        return '/habits/new', HABIT, Habit, 0
    if request.param == 'checkin':
        habit_id = seed_habit(user_id)
        return f'/habits/check_in/{habit_id}', {'time': '12:00', 'note': 'Read a page'}, Record, 0
    return '/todos/new', {
        'event': 'Buy a book', 'date': (date.today() + timedelta(days=1)).strftime('%d/%m/%Y'),
        'time': '12:00',
    }, Todo, 0


@pytest.mark.parametrize(('method', 'body'), [
    ('GET', 'json'), ('HEAD', 'json'), ('GET', 'malformed'), ('GET', 'none'),
])
def test_method_safe_requests_render_without_writing(signed_in, write_form, monkeypatch, method, body):
    _, send, _, _ = signed_in
    path, payload, model, before = write_form
    commit = Mock(wraps=db.session.commit)
    monkeypatch.setattr(db.session, 'commit', commit)
    if body == 'json':
        response = send(method, path, json=payload)
    elif body == 'malformed':
        response = send(method, path, data='{', content_type='application/json')
    else:
        response = send(method, path)
    assert response.status_code == 200 and response.mimetype == 'text/html'
    assert db.session.query(model).count() == before
    commit.assert_not_called()


@pytest.mark.parametrize('encoding', ['json', 'form'])
def test_method_post_preserves_json_and_form_writes(signed_in, write_form, encoding):
    _, send, user_id, token = signed_in
    path, payload, model, before = write_form
    if encoding == 'json':
        response = send('POST', path, json={**payload, 'user_id': 9999}, headers=csrf_header(token))
        assert response.status_code == 200 and response.json['success']
    else:
        response = send('POST', path, data={**payload, 'csrf_token': token})
        assert response.status_code == 302 and response.location == '/'
    assert db.session.query(model).count() == before + 1
    row = db.session.query(model).order_by(model.id.desc()).first()
    assert (row.habit.user_id if model is Record else row.user_id) == user_id


def test_method_checkin_cannot_write_another_users_habit(sign_in):
    _, send, _, token = sign_in('method-user-a')
    _, _, other_id, _ = sign_in('method-user-b')
    habit_id = seed_habit(other_id)
    response = send('POST', f'/habits/check_in/{habit_id}',
                    json={'time': '12:00'}, headers=csrf_header(token))
    assert response.status_code == 200 and response.json['success'] is False
    assert today_records(habit_id) == 0


@pytest.fixture
def temp_image(signed_in, tmp_path):
    _, _, user_id, _ = signed_in
    directory = tmp_path / 'cards' / 'tmp' / str(user_id)
    directory.mkdir(parents=True)
    path = directory / ('a' * 32 + '.png')
    path.write_bytes(PNG)
    return path


@pytest.mark.parametrize('token', [None, 'invalid-token'])
@pytest.mark.parametrize('source', ['base64', 'temporary'])
def test_csrf_save_rejects_before_writing_images(signed_in, temp_image, tmp_path, token, source):
    _, send, _, _ = signed_in
    payload = ({'image_data': data_url(PNG, 'image/png')} if source == 'base64'
               else {'image_id': temp_image.stem})
    response = send('POST', CARD_SAVE, json=payload, headers=csrf_header(token))
    assert response.status_code == 400
    assert response.json == {'success': False, 'error': 'csrf_validation_failed'}
    assert db.session.query(Card).count() == 0
    assert [path for path in tmp_path.rglob('*') if path.is_file()] == [temp_image]
    assert temp_image.read_bytes() == PNG


@pytest.mark.parametrize(('content_type', 'body', 'status', 'error'), [
    ('text/plain', '{"quote":"hello"}', 415, 'unsupported_media_type'),
    ('application/x-www-form-urlencoded', 'quote=hello', 415, 'unsupported_media_type'),
    ('application/json', '{"quote":', 400, 'invalid_card_data'),
    ('application/json', '[]', 400, 'invalid_card_data'),
    ('application/json', 'null', 400, 'invalid_card_data'),
])
def test_csrf_save_rejects_wrong_mime_or_json_shape(signed_in, content_type, body, status, error):
    _, send, _, token = signed_in
    response = send('POST', CARD_SAVE, data=body, content_type=content_type, headers=csrf_header(token))
    assert response.status_code == status
    assert response.json == {'success': False, 'error': error}
    assert db.session.query(Card).count() == 0


@pytest.mark.parametrize('source', ['none', 'base64', 'temporary'])
def test_csrf_valid_card_creation_and_overwrite(signed_in, temp_image, tmp_path, source):
    _, send, user_id, token = signed_in
    payload = {'quote': 'Keep going', 'user_id': 9999}
    if source == 'base64':
        payload['image_data'] = data_url(PNG, 'image/png')
    elif source == 'temporary':
        payload['image_id'] = temp_image.stem
    headers = csrf_header(token)
    response = send('POST', CARD_SAVE, data=json.dumps(payload), headers=headers,
                    content_type='application/vnd.dailydot+json')
    assert response.status_code == 200 and response.json['created']
    card_id = response.json['card_id']
    assert db.session.query(Card.user_id).filter_by(id=card_id).scalar() == user_id
    image_url = db.session.query(Card.image_url).filter_by(id=card_id).scalar()
    if source == 'none':
        assert image_url is None
    else:
        assert (tmp_path / image_url.removeprefix('/static/')).read_bytes() == PNG

    duplicate = send('POST', CARD_SAVE, json=payload, headers=headers)
    assert duplicate.status_code == 200 and duplicate.json == {'success': False, 'reason': 'exists'}
    overwrite = send('POST', CARD_SAVE, json={**payload, 'quote': 'Updated', 'overwrite': True},
                     headers=headers)
    assert overwrite.status_code == 200 and overwrite.json['updated']
    assert overwrite.json['card_id'] == card_id
    assert db.session.query(Card.quote).filter_by(id=card_id).scalar() == 'Updated'
    assert db.session.query(Card).count() == 1
    assert send('GET', '/cards/stack').status_code == 200


def test_csrf_valid_token_does_not_bypass_image_url_restriction(signed_in):
    _, send, _, token = signed_in
    response = send('POST', CARD_SAVE, headers=csrf_header(token),
                    json={'image_url': 'https://untrusted.example/image.png'})
    assert response.status_code == 400
    assert response.json == {'success': False, 'error': 'image_url_not_supported'}
    assert db.session.query(Card).count() == 0


def test_csrf_token_does_not_replace_card_authentication(browser):
    _, send = browser()
    token = csrf_token(send('GET', '/auth/login'))
    response = send('POST', CARD_SAVE, json={}, headers=csrf_header(token))
    assert response.status_code == 302 and '/auth/login' in response.location
    assert db.session.query(Card).count() == 0


def test_csrf_delete_and_overwrite_fail_without_token_and_preserve_card(signed_in, tmp_path):
    _, send, _, token = signed_in
    headers = csrf_header(token)
    created = send('POST', CARD_SAVE, headers=headers,
                   json={'quote': 'Original', 'image_data': data_url(PNG, 'image/png')})
    assert created.status_code == 200 and created.json['success']
    card_id = created.json['card_id']
    path = f'/cards/api/card/{card_id}'
    reference = db.session.query(Card.image_url).filter_by(id=card_id).scalar()
    image = tmp_path / reference.removeprefix('/static/')
    for rejected in (None, 'invalid-token'):
        denied_headers = csrf_header(rejected)
        for method, endpoint, body in (
            ('DELETE', path, {}),
            ('POST', CARD_SAVE, {'json': {'overwrite': True, 'quote': 'Changed'}}),
        ):
            response = send(method, endpoint, headers=denied_headers, **body)
            assert response.status_code == 400
            assert response.json == {'success': False, 'error': 'csrf_validation_failed'}
            assert db.session.query(Card.quote, Card.image_url).filter_by(id=card_id).one() == (
                'Original', reference,
            )
            assert image.read_bytes() == PNG
    assert send('DELETE', path, headers=headers).json == {'success': True}
    assert db.session.query(Card).count() == 0
    for missing in (path, '/cards/api/card/9999'):
        assert send('DELETE', missing, headers=headers).status_code == 404
        assert db.session.query(Card).count() == 0


def test_csrf_tokens_and_card_ownership_stay_bound_to_each_client(sign_in):
    _, send_a, id_a, token_a = sign_in('method-user-a')
    _, send_b, id_b, token_b = sign_in('method-user-b')
    assert id_a != id_b and token_a != token_b
    rejected = send_b('POST', CARD_SAVE, headers=csrf_header(token_a), json={})
    assert rejected.status_code == 400
    assert rejected.json == {'success': False, 'error': 'csrf_validation_failed'}
    assert db.session.query(Card).count() == 0

    created = send_b('POST', CARD_SAVE, headers=csrf_header(token_b), json={'user_id': id_a})
    assert created.status_code == 200 and created.json['success']
    card_id = created.json['card_id']
    path = f'/cards/api/card/{card_id}'
    assert db.session.query(Card.user_id).filter_by(id=card_id).scalar() == id_b
    assert send_a('DELETE', path, headers=csrf_header(token_a)).status_code == 404
    assert db.session.query(Card.user_id).filter_by(id=card_id).scalar() == id_b
    assert send_b('DELETE', path, headers=csrf_header(token_b)).json['success']
    assert db.session.query(Card).count() == 0
