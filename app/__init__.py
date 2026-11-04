"""Factory exports and a process-local, lazily created default application."""

from importlib import import_module
from threading import RLock

from app.factory import create_app
from app.extensions import db, migrate, login, csrf

_default_app = None
_default_app_lock = RLock()
_creating_default_app = False
_management_helpers = frozenset({
    'reset_db', 'init_migrations', 'upgrade_db', 'create_migration',
})


def _get_default_app():
    global _default_app, _creating_default_app
    with _default_app_lock:
        if _default_app is not None:
            return _default_app
        if _creating_default_app:
            raise RuntimeError('Recursive default application initialization.')
        _creating_default_app = True
        try:
            application = create_app()
            _default_app = application
            return application
        finally:
            _creating_default_app = False


def __getattr__(name):
    if name in {'app', 'application'}:
        return _get_default_app()
    if name in _management_helpers:
        return getattr(import_module('.debug_utils', __name__), name)
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')

# Keep model imports so SQLAlchemy can map them.
# Routes are now registered via blueprints in the factory.
from app import models  # noqa: E402, F401
