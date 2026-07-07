"""Logging configuration for DailyDot.

Usage in ``app/factory.py``:

    from app.logging_config import setup_logging
    setup_logging(app)
"""

import logging
import os
from logging.handlers import RotatingFileHandler


def setup_logging(app):
    """Configure structured logging for the application.

    - Development: human-readable output to stderr (already provided by Flask).
    - Production: ALSO write to a rotating file.
    """
    log_level = logging.DEBUG if app.debug else logging.INFO

    # File handler (production)
    log_dir = os.path.join(app.root_path, "..", "logs")
    os.makedirs(log_dir, exist_ok=True)
    file_handler = RotatingFileHandler(
        os.path.join(log_dir, "dailydot.log"),
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]"
    ))
    app.logger.addHandler(file_handler)
    app.logger.setLevel(log_level)

    app.logger.info("DailyDot starting — log level: %s", logging.getLevelName(log_level))
