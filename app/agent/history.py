"""Rebuild bounded completed turns; never replay historical tool execution."""
from copy import deepcopy
import json

from app.agent import conversations as c

MAX_HISTORY_TURNS = 6
MAX_HISTORY_CHARACTERS = 12000
PROTOCOL_FIELDS = ('role', 'content', 'tool_calls', 'tool_call_id', 'tool_name')


def rebuild_turn(turn):
    rows = turn['messages']
    c.require(turn['status'] == 'completed' and bool(rows), 'chat_history_invalid')
    c.require([m['position'] for m in rows] == list(range(len(rows))), 'chat_history_invalid')
    c.require(rows[0]['role'] == 'user' and type(rows[0]['content']) is str, 'chat_history_invalid')
    c.require(not any(rows[0].get(key) for key in ('tool_calls', 'tool_call_id', 'tool_name')),
              'chat_history_invalid')
    messages = [{key: deepcopy(row[key]) for key in PROTOCOL_FIELDS if row.get(key) is not None}
                for row in rows]
    c.validated_messages(messages[1:], True)
    c.check_message_budget(messages)
    mapping = {}
    for message in messages:
        for call in message.get('tool_calls', []):
            mapped = f"hist_{turn['id']:x}_{len(mapping):x}"
            c.require(len(mapped) <= 100, 'chat_history_invalid')
            mapping[call['id']] = mapped
            call['id'] = mapped
        if message['role'] == 'tool':
            message['tool_call_id'] = mapping[message['tool_call_id']]
        # Storage-only absent fields must not become null SDK tool_calls.
        if not message.get('tool_calls'):
            message.pop('tool_calls', None)
        if message['role'] != 'tool':
            message.pop('tool_call_id', None)
            message.pop('tool_name', None)
        else:
            message.pop('tool_name', None)  # ID pairs the OpenAI-compatible wire protocol.
    stamp = turn['created_at'].isoformat()
    messages[0]['content'] = f'【历史轮次创建时间 {stamp}；统计以工具返回日期范围为准】\n' + messages[0]['content']
    return messages


def build_history(turns):
    """Input is an owner-checked database tail (at most seven completed turns)."""
    c.require(type(turns) is list and len(turns) <= MAX_HISTORY_TURNS + 1, 'chat_history_invalid')
    c.require(len({turn['id'] for turn in turns}) == len(turns)
              and [turn['turn_no'] for turn in turns] == sorted({turn['turn_no'] for turn in turns}),
              'chat_history_invalid')
    selected, size = [], 0
    for turn in reversed(turns[-MAX_HISTORY_TURNS:]):
        messages = rebuild_turn(turn)
        addition = len(json.dumps(messages, ensure_ascii=False, allow_nan=False))
        if size + addition > MAX_HISTORY_CHARACTERS:
            break  # Keep a contiguous recent suffix; never split a tool protocol.
        selected.insert(0, messages)
        size += addition
    return {'messages': [m for group in selected for m in group],
            'history_truncated': len(selected) < len(turns)}
