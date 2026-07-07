"""Standard JSON response helpers — keep API responses consistent."""

from flask import jsonify


def success_response(data=None, message=None, status=200):
    """Return a standard success JSON response."""
    payload = {'success': True}
    if data is not None:
        payload['data'] = data
    if message is not None:
        payload['message'] = message
    return jsonify(payload), status


def error_response(message, status=400):
    """Return a standard error JSON response."""
    return jsonify({'success': False, 'message': message}), status
