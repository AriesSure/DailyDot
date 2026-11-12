"""Bounded read adapters: identity is bound by the server, never tool arguments."""
from datetime import date, timedelta
import sqlalchemy as sa
from app.extensions import db
from app.models import Habit, Record


class AgentError(ValueError):
    pass


def tool_schema(name, description, properties):
    return {'type': 'function', 'function': {
        'name': name, 'description': description,
        'parameters': {'type': 'object', 'properties': properties, 'additionalProperties': False}}}


TOOLS = [tool_schema('list_user_habits', '查询当前用户的习惯；不接受参数。', {}),
         tool_schema('get_checkin_summary', '查询日期范围内真实打卡次数，不是完成率。默认最近7天。', {
             'days': {'type': 'integer', 'enum': [7, 30]},
             'start_date': {'type': 'string', 'description': 'ISO日期，须同时提供end_date'},
             'end_date': {'type': 'string', 'description': 'ISO日期，最多90天，不得晚于今天'}})]


def validate(name, args):
    if type(args) is not dict:
        raise AgentError('agent_tool_arguments_invalid')
    if name == 'list_user_habits':
        if args:
            raise AgentError('agent_tool_arguments_invalid')
        return None
    if name != 'get_checkin_summary':
        raise AgentError('agent_tool_unknown')
    today = date.today()
    if not args or set(args) == {'days'}:
        days = args.get('days', 7)
        if type(days) is not int or days not in (7, 30):
            raise AgentError('agent_tool_arguments_invalid')
        return today - timedelta(days=days - 1), today
    if set(args) != {'start_date', 'end_date'}:
        raise AgentError('agent_tool_arguments_invalid')
    try:
        start, end = (date.fromisoformat(args[k]) for k in ('start_date', 'end_date'))
        if any(type(args[k]) is not str or args[k] != d.isoformat()
               for k, d in zip(('start_date', 'end_date'), (start, end))):
            raise ValueError()
    except (TypeError, ValueError):
        raise AgentError('agent_tool_arguments_invalid') from None
    if not start <= end <= today or (end - start).days >= 90:
        raise AgentError('agent_date_range_invalid')
    return start, end


def query_tool(user_id, name, args):
    interval = validate(name, args)
    # No existing list/range-count service is bounded; isolate this read adapter.
    with db.session.no_autoflush:
        habits = db.session.query(Habit).filter_by(user_id=user_id).order_by(Habit.id).limit(101).all()
        if len(habits) > 100:
            raise AgentError('agent_result_limit')
        items = [{'id': h.id, 'name': h.habit_name, 'frequency': h.frequency,
                  'note': (h.note or '')[:256]} for h in habits]
        if interval is None:
            return {'habits': items}
        start, end = interval
        rows = db.session.query(Record.habit_id, sa.func.count(Record.id)).join(Habit).filter(
            Habit.user_id == user_id, Record.checkin_date.between(start, end)
        ).group_by(Record.habit_id).all()
        counts = dict(rows)
        return {'start_date': start.isoformat(), 'end_date': end.isoformat(),
                'metric': 'checkin_count_not_completion_rate',
                'habits': [{'id': h['id'], 'name': h['name'], 'checkins': counts.get(h['id'], 0)} for h in items]}
