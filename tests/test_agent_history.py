"""Protocol assembly tests do not assert real-model language understanding."""
from copy import deepcopy
from datetime import datetime, timezone
import json

import pytest

from app.agent import history as h, conversations as c


def turn(number, *, tools=1, content='answer'):
    messages = [{'role': 'user', 'content': 'question', 'position': 0}]
    if tools:
        calls = [{'id': f'call_{i}', 'type': 'function', 'function': {
            'name': 'list_user_habits', 'arguments': '{}'}} for i in range(tools)]
        messages.append({'role': 'assistant', 'tool_calls': calls, 'position': 1})
        messages += [{'role': 'tool', 'tool_call_id': call['id'], 'tool_name': 'list_user_habits',
                      'content': '{}', 'position': i + 2} for i, call in enumerate(calls)]
    messages.append({'role': 'assistant', 'content': content, 'position': len(messages)})
    return {'id': number, 'turn_no': number, 'status': 'completed',
            'created_at': datetime(2026, 1, 1, tzinfo=timezone.utc), 'messages': messages}


@pytest.mark.parametrize('count', [0, 1, 5, 6, 7])
def test_complete_turn_tail_and_original_ids(count):
    turns = [turn(i + 1) for i in range(count)]
    before = deepcopy(turns)
    result = h.build_history(turns)
    assert len([m for m in result['messages'] if m['role'] == 'user']) == min(count, 6)
    assert result['history_truncated'] == (count > 6)
    assert before == turns
    calls = [call['id'] for m in result['messages'] for call in m.get('tool_calls', [])]
    results = [m['tool_call_id'] for m in result['messages'] if m['role'] == 'tool']
    assert calls == results and len(calls) == len(set(calls))
    assert all(len(cid) <= 100 for cid in calls)


def test_multiple_tool_pairs_and_time_annotation():
    result = h.build_history([turn(1, tools=5)])['messages']
    assert len(result) == 8
    assert '2026-01-01' in result[0]['content']
    assert all(m['role'] != 'system' for m in result)


@pytest.mark.parametrize('corrupt', ['orphan', 'missing', 'duplicate', 'position', 'system', 'failed',
                                     'pending', 'interrupted'])
def test_corrupt_history_rejected(corrupt):
    row = turn(1)
    if corrupt == 'orphan':
        row['messages'][2]['tool_call_id'] = 'unknown'
    elif corrupt == 'missing':
        del row['messages'][2]
        for i, message in enumerate(row['messages']):
            message['position'] = i
    elif corrupt == 'duplicate':
        row['messages'][1]['tool_calls'] *= 2
    elif corrupt == 'position':
        row['messages'][2]['position'] = 9
    elif corrupt == 'system':
        row['messages'][0]['role'] = 'system'
    else:
        row['status'] = corrupt
    with pytest.raises(c.ConversationError):
        h.build_history([row])


def test_oversized_turn_omitted_whole():
    row = turn(1, content='x' * 13000)
    result = h.build_history([row])
    assert result == {'messages': [], 'history_truncated': True}


def test_character_budget_keeps_recent_contiguous_turns():
    result = h.build_history([turn(i, content='x' * 4000) for i in range(1, 7)])
    assert result['history_truncated']
    assert len(json.dumps(result['messages'], ensure_ascii=False)) <= 12000
    users = [m for m in result['messages'] if m['role'] == 'user']
    assert len(users) == 2
    calls = [call['id'] for m in result['messages'] for call in m.get('tool_calls', [])]
    assert calls == ['hist_5_0', 'hist_6_0']


@pytest.mark.parametrize('overflow', [0, 1])
def test_exact_history_character_limit(overflow):
    row = turn(1, tools=0, content='')
    # Keep the completed protocol valid while deriving the serialized overhead.
    row['messages'][-1]['content'] = 'x'
    base = len(json.dumps(h.rebuild_turn(row), ensure_ascii=False, allow_nan=False))
    row['messages'][-1]['content'] = 'x' * (12000 - base + 1 + overflow)
    result = h.build_history([row])
    assert result['history_truncated'] == bool(overflow)
    assert bool(result['messages']) != bool(overflow)
