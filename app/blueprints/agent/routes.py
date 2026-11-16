"""Thin HTTP adapter; all ownership, idempotency and admission remain in services."""
from datetime import timezone
import json
import re

from flask import Blueprint, jsonify, request
from flask_login import current_user
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.exceptions import BadRequest, HTTPException, RequestEntityTooLarge, UnsupportedMediaType

from app.agent import conversations as c, dialogue

agent_bp = Blueprint('agent', __name__, url_prefix='/agent')
MAX_BODY = 16 * 1024
ERRORS = {
    'invalid_json': (400, 'JSON 格式不正确。'),
    'invalid_content_type': (415, '请使用 JSON 请求。'),
    'invalid_request': (400, '请求参数不正确。'),
    'unauthorized': (401, '请先登录。'),
    'not_found': (404, '无法访问该会话或请求。'),
    'request_conflict': (409, '请求编号已用于其他问题。'),
    'conversation_busy': (409, '当前用户已有请求正在执行。'),
    'rate_limited': (429, '当前请求额度已满，请稍后再试。'),
    'chat_persistence_failed': (503, '暂时无法读取或保存数据。'),
    'request_state_unresolved': (503, '请求状态尚未确认，请使用原会话和请求编号查询。'),
    'result_unavailable': (503, '暂时无法读取完整结果。'),
    'service_unavailable': (503, '服务暂时不可用。'),
    'internal_error': (500, '服务处理失败。'),
    'csrf_failed': (400, '安全验证失败，请刷新页面后重试。'),
    'request_too_large': (413, '请求体超过大小限制。'),
    'method_not_allowed': (405, '不支持此请求方法。'),
}
SERVICE_ERRORS = {
    'chat_not_found': 'not_found', 'chat_request_conflict': 'request_conflict',
    'chat_busy': 'conversation_busy', 'agent_user_busy': 'conversation_busy',
    'agent_global_capacity': 'rate_limited', 'agent_rate_limited': 'rate_limited',
    'agent_admission_unavailable': 'service_unavailable', 'chat_authentication_required': 'unauthorized',
    'chat_persistence_failed': 'chat_persistence_failed', 'chat_result_unavailable': 'result_unavailable',
    'chat_protocol_invalid': 'result_unavailable', 'chat_protocol_incomplete': 'result_unavailable',
    'chat_request_invalid': 'invalid_request', 'chat_page_invalid': 'invalid_request',
    'chat_content_invalid': 'invalid_request',
}


def is_agent_path():
    return request.path == '/agent' or request.path.startswith('/agent/')


def error(code):
    status, message = ERRORS[code]
    return jsonify(success=False, data=None, error={'code': code, 'message': message}), status


def success(data, status=200):
    return jsonify(success=True, data=data, error=None), status


def service_error(code):
    return error(SERVICE_ERRORS.get(code, 'internal_error'))


def http_error(exc):
    if exc.code >= 500:
        return error('internal_error')
    if not current_user.is_authenticated:
        return error('unauthorized')
    return error({400: 'invalid_request', 401: 'unauthorized', 403: 'unauthorized', 404: 'not_found',
                  405: 'method_not_allowed', 413: 'request_too_large',
                  415: 'invalid_content_type'}.get(exc.code, 'internal_error'))


@agent_bp.before_request
def authenticate():
    if not current_user.is_authenticated:
        return error('unauthorized')


@agent_bp.errorhandler(c.ConversationError)
def conversation_error(exc):
    code = exc.args[0] if exc.args and type(exc.args[0]) is str else None
    return service_error(code)


@agent_bp.errorhandler(Exception)
def unexpected_error(exc):
    if isinstance(exc, HTTPException):
        return http_error(exc)
    if isinstance(exc, SQLAlchemyError):
        return error('chat_persistence_failed')
    return error('internal_error')


def uuid32(value, *, missing=False):
    if type(value) is not str or re.fullmatch('[0-9a-f]{32}', value) is None:
        if missing:
            raise c.ConversationError('chat_not_found')
        raise BadRequest()
    return value


def body(fields):
    if request.mimetype != 'application/json':
        raise UnsupportedMediaType()
    if request.content_length is not None and request.content_length > MAX_BODY:
        raise RequestEntityTooLarge()
    raw = request.stream.read(MAX_BODY + 1)
    if len(raw) > MAX_BODY:
        raise RequestEntityTooLarge()

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError()
            result[key] = value
        return result

    def invalid_constant(_):
        raise ValueError()

    try:
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid_constant)
    except (ValueError, UnicodeError, RecursionError):
        return None, error('invalid_json')
    if type(value) is not dict or set(value) != set(fields):
        return None, error('invalid_request')
    return value, None


def page(default, maximum):
    if set(request.args) - {'offset', 'limit'} or any(len(v) != 1 for _, v in request.args.lists()):
        raise BadRequest()
    result = {}
    for name, fallback, cap in [('offset', 0, 10000), ('limit', default, maximum)]:
        value = request.args.get(name, str(fallback))
        if re.fullmatch('[0-9]{1,5}', value) is None:
            raise BadRequest()
        result[name] = int(value)
        if not (0 if name == 'offset' else 1) <= result[name] <= cap:
            raise BadRequest()
    return result


def utc(value):
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)  # Existing chat models store naive UTC.
    return value.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def conversation_dto(row):
    return {'conversation_id': row['id'], 'title': row['title'],
            'created_at': utc(row['created_at']), 'updated_at': utc(row['updated_at'])}


def public_tool_content(message):
    """Display only business facts; storage IDs stay in the persisted audit protocol."""
    try:
        value = json.loads(message['content'])
        if type(value) is not dict or type(value.get('habits')) is not list:
            raise ValueError()
        if message['tool_name'] == 'list_user_habits':
            fields, root_fields = ('name', 'frequency', 'note'), ()
        else:
            fields, root_fields = ('name', 'checkins'), ('start_date', 'end_date', 'metric')
        result = {key: value[key] for key in root_fields}
        result['habits'] = [{key: row[key] for key in fields} for row in value['habits']]
        for row in result['habits']:
            if type(row['name']) is not str:
                raise ValueError()
            if 'checkins' in row:
                if type(row['checkins']) is not int or row['checkins'] < 0:
                    raise ValueError()
            elif (row['frequency'] is not None and type(row['frequency']) is not str) or type(row['note']) is not str:
                raise ValueError()
        if root_fields and (any(type(result[key]) is not str for key in root_fields)
                            or result['metric'] != 'checkin_count_not_completion_rate'):
            raise ValueError()
        return json.dumps(result, ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError, KeyError):
        raise c.ConversationError('chat_result_unavailable') from None


def turn_dto(turn):
    rows = turn['messages']
    fields = ('role', 'content', 'tool_calls', 'tool_call_id', 'tool_name')
    if (turn['status'] not in ('pending', 'completed', 'failed', 'interrupted')
            or not rows or rows[0]['role'] != 'user' or type(rows[0]['content']) is not str
            or [m['position'] for m in rows] != list(range(len(rows)))
            or any(rows[0].get(key) for key in fields[2:])):
        raise c.ConversationError('chat_result_unavailable')
    messages = [{key: row[key] for key in fields if row.get(key) is not None} for row in rows]
    c.validated_messages(messages[1:], turn['status'] == 'completed')
    c.check_message_budget(messages)
    for message in messages:
        if message['role'] == 'tool':
            message['content'] = public_tool_content(message)
    return {'request_id': turn['client_request_id'], 'turn_no': turn['turn_no'], 'status': turn['status'],
            'created_at': utc(turn['created_at']), 'completed_at': utc(turn['finished_at']),
            'history_truncated': None,
            'messages': [dict(position=row['position'], **message) for row, message in zip(rows, messages)]}


@agent_bp.post('/conversations', provide_automatic_options=False)
def create():
    value, failure = body(())
    if failure:
        return failure
    return success(conversation_dto(c.create_conversation()), 201)


@agent_bp.get('/conversations', provide_automatic_options=False)
def conversations():
    return success({'conversations': [conversation_dto(row) for row in c.list_conversations(**page(20, 100))]})


@agent_bp.get('/conversations/<cid>/history', provide_automatic_options=False)
def history(cid):
    uuid32(cid, missing=True)
    return success({'conversation_id': cid, 'turns': [turn_dto(row) for row in c.history(cid, **page(10, 20))]})


@agent_bp.post('/conversations/<cid>/messages', provide_automatic_options=False)
def messages(cid):
    uuid32(cid, missing=True)
    value, failure = body(('request_id', 'question'))
    if failure:
        return failure
    rid = uuid32(value['request_id'])
    question = value['question']
    if type(question) is not str or not question.strip() or len(question) > 2000:
        return error('invalid_request')
    result = dialogue.send_message(cid, rid, question)
    if result.get('recovery') == 'state_unresolved':
        return error('request_state_unresolved')
    if 'turn' not in result:
        response, status = service_error(result.get('error_code'))
        retry = result.get('retry_after_seconds')
        if status == 429 and type(retry) is int and 0 < retry <= 3600:
            response.headers['Retry-After'] = str(retry)
        return response, status
    data = c.get_request_result(cid, rid)
    if not result.get('duplicate') and type(result.get('history_truncated')) is bool:
        data['history_truncated'] = result['history_truncated']
    return success(data, 202 if data['status'] == 'pending' else 200)


@agent_bp.get('/conversations/<cid>/requests/<rid>', provide_automatic_options=False)
def request_status(cid, rid):
    uuid32(cid, missing=True)
    uuid32(rid)
    return success(c.get_request_result(cid, rid))
