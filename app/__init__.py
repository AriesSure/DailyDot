"""Application entry-point — created by the factory.

All significant initialisation lives in ``app.factory.create_app()``.
This module simply re-exports the created ``app`` instance and the
most-used extension objects so that existing import patterns such as
``from app import app, db`` continue to work unchanged.
"""

from app.factory import create_app
from app.extensions import db, migrate, login, csrf

app = create_app()

# Keep model imports so SQLAlchemy can map them.
# Routes are now registered via blueprints in the factory.
from app import models  # noqa: E402, F401
from app.debug_utils import (  # noqa: E402, F401
    reset_db,
    init_migrations,
    upgrade_db,
    create_migration,
)
