"""Centralised error-handler registration (called from factory)."""

from flask import render_template


def register_error_handlers(app):
    """Register ``@app.errorhandler`` for common HTTP codes."""

    @app.errorhandler(403)
    def error_403(_error):
        return render_template('errors/403.html', title='Error'), 403

    @app.errorhandler(404)
    def error_404(_error):
        return render_template('errors/404.html', title='Error'), 404

    @app.errorhandler(413)
    def error_413(_error):
        return render_template('errors/413.html', title='Error'), 413

    @app.errorhandler(500)
    def error_500(_error):
        return render_template('errors/500.html', title='Error'), 500
