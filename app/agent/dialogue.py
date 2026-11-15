"""Internal orchestration; no HTTP endpoint and no model retry."""
from app.agent import conversations as c
from app.agent.service import run_agent_with_history


def send_message(cid, rid, question, *, model=None):
    """Create once, execute outside write transactions, then finish with CAS.

    A persistence failure with uncertain commit outcome stays unresolved; callers
    query the same request ID, never automatically issue another model request.
    """
    started = False
    try:
        uid = c.identity()
        # Normalize before both persistence and execution so the digest and user
        # trace refer to exactly the same question. Keep the existing Agent limit.
        question = c.normalize(question)
        c.require(len(question) <= 2000, 'chat_content_invalid')
        begun = c.begin_admitted_turn(cid, rid, question)
        if not begun['created']:
            return {'ok': begun['turn']['status'] == 'completed', 'duplicate': True,
                    'turn': begun['turn'], 'answer': None, 'error_code': None}
        started = True
        turns = c.recent_completed(cid)
        result = run_agent_with_history(question, user_id=uid, turns=turns, model=model)
        status = 'completed' if result['ok'] else 'failed'
        turn = c.finish_turn(cid, rid, status=status, messages=result['messages'][1:],
                             error_code=None if result['ok'] else 'chat_execution_failed')
        return {'ok': result['ok'], 'duplicate': False, 'turn': turn,
                'answer': result['answer'], 'error_code': result['error_code'],
                'history_truncated': result['history_truncated']}
    except Exception as exc:
        code = 'chat_execution_failed'
        if isinstance(exc, c.ConversationError):
            allowed = {'chat_not_found', 'chat_busy', 'chat_request_conflict', 'chat_request_invalid',
                       'chat_authentication_required', 'chat_content_invalid', 'chat_ownership_lost',
                       'chat_persistence_failed', 'chat_protocol_invalid', 'chat_protocol_incomplete',
                       'agent_user_busy', 'agent_global_capacity', 'agent_rate_limited',
                       'agent_admission_unavailable'}
            if exc.args and type(exc.args[0]) is str and exc.args[0] in allowed:
                code = exc.args[0]
        recovery = 'not_needed'
        # Validation failure is known to have rolled back. Discard an invalid
        # trace and safely finish failed. Never compensate uncertain DB commits.
        if code == 'chat_persistence_failed':
            # Even begin_turn() may have committed before losing its ACK.
            # Query the original cid/rid; never compensate or retry execution.
            recovery = 'state_unresolved'
        elif started and code != 'chat_ownership_lost':
            try:
                c.finish_turn(cid, rid, status='failed', messages=[], error_code='chat_execution_failed')
                recovery = 'failed_saved'
            except Exception:
                recovery = 'state_unresolved'
        elif started:
            recovery = 'state_unresolved'
        result = {'ok': False, 'answer': None, 'error_code': code, 'recovery': recovery}
        if code == 'agent_rate_limited' and type(getattr(exc, 'retry_after_seconds', None)) is int:
            result['retry_after_seconds'] = exc.retry_after_seconds
        return result
