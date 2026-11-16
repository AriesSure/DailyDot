"""Centralised error-handler registration (called from factory)."""

from flask import render_template
from flask_login import current_user
from flask_wtf.csrf import CSRFError
from werkzeug.exceptions import HTTPException

from app.blueprints.agent.routes import is_agent_path, error, http_error


def register_error_handlers(app):
    """Register ``@app.errorhandler`` for common HTTP codes."""

    @app.after_request
    def agent_no_store(response):
        if is_agent_path():
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.errorhandler(CSRFError)
    def csrf_error(exc):
        if is_agent_path():
            return error('csrf_failed' if current_user.is_authenticated else 'unauthorized')
        return exc.get_response()

    @app.errorhandler(HTTPException)
    def agent_http_error(exc):
        if is_agent_path():
            return http_error(exc)
        return exc

    @app.errorhandler(403)
    def error_403(_error):
        if is_agent_path():
            return http_error(_error)
        return render_template('errors/403.html', title='Error'), 403

    @app.errorhandler(404)
    def error_404(_error):
        if is_agent_path():
            return http_error(_error)
        return render_template('errors/404.html', title='Error'), 404

    @app.errorhandler(413)
    def error_413(_error):
        if is_agent_path():
            return http_error(_error)
        return render_template('errors/413.html', title='Error'), 413

    @app.errorhandler(500)
    def error_500(_error):
        if is_agent_path():
            return http_error(_error)
        return render_template('errors/500.html', title='Error'), 500
