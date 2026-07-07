"""Flask extensions initialized in lazy (unbound) state, then init_app() in factory."""

from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect

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
