"""Application factory — create_app() replaces the old module-level singleton."""

import os

from flask import Flask
from jinja2 import StrictUndefined

from app.config import config_by_name
from app.extensions import db, init_extensions
import sqlalchemy as sa


def create_app(config_name: str | None = None) -> Flask:
    """Create and configure the Flask application.

    Parameters
    ----------
    config_name : str, optional
        One of 'development', 'testing', 'production'.
        Falls back to the ``FLASK_ENV`` env-var, then 'development'.

    Returns
    -------
    Flask
        Fully initialised application instance.
    """
    if config_name is None:
        config_name = os.environ.get('FLASK_ENV', 'development')

    app = Flask(__name__)
    app.jinja_env.undefined = StrictUndefined
    app.config.from_object(config_by_name[config_name])

    # ── Extensions ──────────────────────────────────────────────
    init_extensions(app)

    # ── Blueprints (stubs now, populated in Phase 2) ────────────
    _register_blueprints(app)

    # ── Error handlers ──────────────────────────────────────────
    from app.error_handlers import register_error_handlers
    register_error_handlers(app)

    # ── Dev safety: auto-create missing tables on SQLite ────────
    _ensure_dev_tables(app)

    # ── Shell context ───────────────────────────────────────────
    import sqlalchemy.orm as so
    from app.extensions import db as _db

    @app.shell_context_processor
    def make_shell_context():
        return dict(db=_db, sa=sa, so=so)

    return app


# ── Helper ──────────────────────────────────────────────────────


def _register_blueprints(app: Flask) -> None:
    """Register all blueprint modules."""
    from app.blueprints.auth.routes import auth_bp
    from app.blueprints.main.routes import main_bp
    from app.blueprints.habits.routes import habits_bp
    from app.blueprints.todos.routes import todos_bp
    from app.blueprints.cards.routes import cards_bp
    from app.blueprints.stats.routes import stats_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(habits_bp)
    app.register_blueprint(todos_bp)
    app.register_blueprint(cards_bp)
    app.register_blueprint(stats_bp)

    # ai_bp will be registered in Phase 4


def _ensure_dev_tables(app: Flask) -> None:
    """Auto-create missing tables on SQLite (dev convenience).

    Avoids ``cards``-table-not-found errors when Alembic migrations
    haven't been run yet.  Harmless to call repeatedly.
    """
    try:
        with app.app_context():
            backend = db.engine.url.get_backend_name()
            if backend != 'sqlite':
                return
            inspector = sa.inspect(db.engine)
            tables = set(inspector.get_table_names())
            if 'cards' not in tables:
                db.create_all()
    except Exception as exc:
        try:
            app.logger.warning("_ensure_dev_tables failed: %s", exc)
        except Exception:
            pass
