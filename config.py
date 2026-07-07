"""Redirect to the new ``app.config`` module.

Existing code that does ``from config import Config`` will continue
to work.  The real configuration now lives in ``app.config.py``.
"""
from app.config import Config  # noqa: F401
