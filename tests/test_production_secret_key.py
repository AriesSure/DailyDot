"""Production factory and session signing regressions with synthetic credentials."""

import runpy
from pathlib import Path
from unittest.mock import Mock

import pytest
import sqlalchemy as sa
from flask import current_app
from flask_login import current_user, login_required

import app as app_package
from app import factory, logging_config
from app.config import ProductionSecretKeyError, _NON_PRODUCTION_SECRET_KEY
from app.extensions import db, login, migrate
from app.models import User


_TEST_KEY_A = 'test-only-production-signing-key-alpha'
_TEST_KEY_B = 'test-only-production-signing-key-bravo'
_ERROR = 'production_secret_key_required'


@pytest.fixture
def factory_without_database(monkeypatch):
    """Keep the real factory, routing and Flask-Login, without database setup."""
    init_db = Mock()
    monkeypatch.setattr(db, 'init_app', init_db)
    monkeypatch.setattr(migrate, 'init_app', Mock())
    monkeypatch.setattr(factory, '_ensure_dev_tables', Mock())
    monkeypatch.setattr(logging_config, 'setup_logging', Mock())
    return init_db


@pytest.mark.parametrize('value', [
    pytest.param(None, id='missing'),
    pytest.param('', id='empty'),
    pytest.param(' ' * 40, id='whitespace'),
    pytest.param(_NON_PRODUCTION_SECRET_KEY.decode('ascii'), id='legacy-fallback'),
    pytest.param('change-this-to-a-long-random-string', id='example-placeholder'),
    pytest.param(' change-this-to-a-long-random-string ', id='padded-placeholder'),
    pytest.param('test-only-short-key', id='too-short'),
])
def test_production_rejects_before_creating_app_or_engine(monkeypatch, caplog, value):
    if value is None:
        monkeypatch.delenv('SECRET_KEY', raising=False)
    else:
        monkeypatch.setenv('SECRET_KEY', value)

    forbidden = Mock(side_effect=AssertionError('production initialized before secret validation'))
    monkeypatch.setattr(factory, 'Flask', forbidden)
    monkeypatch.setattr(factory, 'init_extensions', forbidden)
    monkeypatch.setattr(factory, '_register_blueprints', forbidden)
    monkeypatch.setattr(factory, '_ensure_dev_tables', forbidden)
    monkeypatch.setattr(sa.engine.Engine, 'connect', forbidden)
    monkeypatch.setattr(db, 'create_all', forbidden)

    with pytest.raises(ProductionSecretKeyError) as error:
        factory.create_app('production')

    assert error.value.args == (_ERROR,)
    assert _ERROR not in caplog.text
    if value and value.strip():
        assert value not in caplog.text
    forbidden.assert_not_called()


def test_production_can_retry_and_reads_key_per_factory_call(monkeypatch, factory_without_database):
    monkeypatch.setenv('FLASK_ENV', 'production')
    monkeypatch.delenv('SECRET_KEY', raising=False)
    for _ in range(2):
        with pytest.raises(ProductionSecretKeyError, match=f'^{_ERROR}$'):
            factory.create_app()
    factory_without_database.assert_not_called()

    monkeypatch.setenv('SECRET_KEY', _TEST_KEY_A)
    first = factory.create_app()
    monkeypatch.setenv('SECRET_KEY', _TEST_KEY_B)
    second = factory.create_app('production')

    assert first is not second
    assert first.config['SECRET_KEY'] == _TEST_KEY_A
    assert second.config['SECRET_KEY'] == _TEST_KEY_B
    assert [call.args[0] for call in factory_without_database.call_args_list] == [first, second]

    monkeypatch.delenv('SECRET_KEY')
    with pytest.raises(ProductionSecretKeyError, match=f'^{_ERROR}$'):
        factory.create_app('production')
    assert factory_without_database.call_count == 2


def test_non_production_profiles_keep_fallback_and_isolation(monkeypatch, factory_without_database):
    monkeypatch.delenv('SECRET_KEY', raising=False)
    monkeypatch.delenv('FLASK_ENV', raising=False)
    monkeypatch.setenv('TEST_DATABASE_PROFILE', 'sqlite-memory')
    monkeypatch.delenv('TEST_DATABASE_URL', raising=False)
    development = factory.create_app()
    testing = factory.create_app('testing')

    assert development.debug is True
    assert testing.testing is True
    assert testing.config['SQLALCHEMY_DATABASE_URI'] == 'sqlite:///:memory:'
    assert development.secret_key == testing.secret_key == _NON_PRODUCTION_SECRET_KEY

    monkeypatch.setenv('SECRET_KEY', _TEST_KEY_A)
    assert factory.create_app('development').secret_key == _TEST_KEY_A
    monkeypatch.setenv('SECRET_KEY', _TEST_KEY_B)
    assert factory.create_app('testing').secret_key == _TEST_KEY_B
    assert development.secret_key == testing.secret_key == _NON_PRODUCTION_SECRET_KEY


def test_lazy_production_wsgi_aliases_and_run_py(monkeypatch, factory_without_database):
    monkeypatch.setattr(app_package, '_default_app', None)
    monkeypatch.setenv('FLASK_ENV', 'production')
    monkeypatch.delenv('SECRET_KEY', raising=False)
    run_path = str(Path(__file__).resolve().parents[1] / 'run.py')

    with pytest.raises(ProductionSecretKeyError, match=f'^{_ERROR}$'):
        _ = app_package.app
    with pytest.raises(ProductionSecretKeyError, match=f'^{_ERROR}$'):
        runpy.run_path(run_path)
    assert app_package._default_app is None
    factory_without_database.assert_not_called()

    monkeypatch.setenv('SECRET_KEY', _TEST_KEY_A)
    application = app_package.application
    assert application.secret_key == _TEST_KEY_A
    assert app_package.app is application
    assert runpy.run_path(run_path)['app'] is application
    factory_without_database.assert_called_once_with(application)


@pytest.mark.parametrize('cookie_config,default_name', [
    ('SESSION_COOKIE_NAME', 'session'),
    ('REMEMBER_COOKIE_NAME', 'remember_token'),
])
def test_production_key_rotation_rejects_old_login_cookies(
        monkeypatch, factory_without_database, cookie_config, default_name):
    user = User(id=42, username='fictional-cookie-user', email='test@example.com')
    password = 'test-only-cookie-password'
    user.set_password(password)
    monkeypatch.setattr(db.session, 'scalar', Mock(return_value=user))
    monkeypatch.setattr(login, '_user_callback', lambda user_id: user if user_id == user.get_id() else None)

    def make_app(key):
        monkeypatch.setenv('SECRET_KEY', key)
        application = factory.create_app('production')
        application.config['WTF_CSRF_ENABLED'] = False

        @application.get('/test-signed-identity')
        @login_required
        def signed_identity():
            return current_user.get_id()

        return application

    def sign_in(client):
        response = client.post('/auth/login', data={
            'username': user.username, 'password': password, 'remember_me': 'y',
        })
        assert response.status_code == 302
        assert client.get('/test-signed-identity').get_data(as_text=True) == user.get_id()

    original = make_app(_TEST_KEY_A)
    original_client = original.test_client()
    sign_in(original_client)
    cookie_name = original.config.get(cookie_config, default_name)
    old_cookie = original_client.get_cookie(cookie_name)
    assert old_cookie is not None

    same_key = make_app(_TEST_KEY_A).test_client()
    same_key.set_cookie(cookie_name, old_cookie.value)
    response = same_key.get('/test-signed-identity')
    assert response.status_code == 200 and response.get_data(as_text=True) == user.get_id()

    rotated = make_app(_TEST_KEY_B).test_client()
    rotated.set_cookie(cookie_name, old_cookie.value)
    response = rotated.get('/test-signed-identity')
    assert response.status_code == 302 and '/auth/login' in response.location

    sign_in(rotated)
    assert rotated.get_cookie(cookie_name).value != old_cookie.value
    assert rotated.get('/auth/logout').status_code == 302
    with rotated.session_transaction() as session:
        assert '_user_id' not in session
    assert rotated.get_cookie(original.config.get('REMEMBER_COOKIE_NAME', 'remember_token')) is None
    assert rotated.get('/test-signed-identity').status_code == 302
