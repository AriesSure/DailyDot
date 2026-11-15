"""Flask extensions initialized in lazy (unbound) state, then init_app() in factory."""

from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect
from _sqlite3 import Connection as SQLiteConnection
from sqlalchemy import event
from sqlalchemy.engine import Engine


@event.listens_for(Engine, 'connect')
def enable_sqlite_foreign_keys(connection, record):
    """Run before transactions on every physical SQLite connection, including binds."""
    if isinstance(connection, SQLiteConnection):
        cursor = connection.cursor()
        try:
            cursor.execute('PRAGMA foreign_keys=ON')
            if cursor.execute('PRAGMA foreign_keys').fetchone() != (1,):
                raise RuntimeError('sqlite_foreign_keys_unavailable')
        finally:
            cursor.close()


db = SQLAlchemy()
migrate = Migrate()
login = LoginManager()
csrf = CSRFProtect()


def init_extensions(app):
    """Bind extensions to a Flask app instance (called from factory)."""
    db.init_app(app)
    migrate.init_app(app, db)
    login.init_app(app)
    login.login_view = 'auth.login'  # Will resolve after blueprint registration
    csrf.init_app(app)
