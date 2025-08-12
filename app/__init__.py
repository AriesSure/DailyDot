from flask import Flask
from config import Config
from jinja2 import StrictUndefined
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect
import sqlalchemy as sa
import sqlalchemy.orm as so


app = Flask(__name__)
app.jinja_env.undefined = StrictUndefined
app.config.from_object(Config)
db = SQLAlchemy(app)
migrate = Migrate(app, db)  # Initialize Flask-Migrate
login = LoginManager(app)
login.login_view = 'login'
csrf = CSRFProtect(app)  # Initialize CSRF Protection


# Development-safe helper: ensure missing tables (like 'cards') exist for SQLite/local.
# This avoids runtime errors when migrations weren't applied yet.
# It will not alter existing tables and is safe to run repeatedly.
def _ensure_dev_tables():
    try:
        # Only attempt this for SQLite/dev use to avoid unintended effects in production
        backend = db.engine.url.get_backend_name()
        if backend != 'sqlite':
            return
        inspector = sa.inspect(db.engine)
        tables = set(inspector.get_table_names())
        # If 'cards' is missing, create all known tables
        if 'cards' not in tables:
            db.create_all()
    except Exception as e:
        # Be silent in prod; in dev we can log a warning
        try:
            app.logger.warning(f"Ensure tables check failed: {e}")
        except Exception:
            pass

from app import views, models
from app.debug_utils import reset_db, init_migrations, upgrade_db, create_migration


# Call the dev ensure within an application context so db.create_all() can run
with app.app_context():
    _ensure_dev_tables()

@app.shell_context_processor
def make_shell_context():
    return dict(
        db=db,
        sa=sa,
        so=so,
        reset_db=reset_db,
        init_migrations=init_migrations,
        upgrade_db=upgrade_db,
        create_migration=create_migration
    )
