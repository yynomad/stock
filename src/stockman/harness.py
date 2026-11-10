"""Bound the model's view without rewriting the LangGraph checkpoint."""
import json
import math

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.utils.function_calling import convert_to_openai_tool


DEFAULT_CONTEXT_BYTES = 48 * 1024
MIN_CONTEXT_BYTES = 4 * 1024
MAX_CONTEXT_BYTES = 1024 * 1024
DEFAULT_CONTEXT_TTL = 86400
MAX_CONTEXT_TTL = 30 * 86400
CONTEXT_TIMESTAMP = "stockman_created_at"


class ContextOverflow(ValueError):
    """The current question and its evidence cannot fit the input allowance."""


def context_bytes(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))


def fresh(created, now, ttl):
    return type(created) in (float, int) and math.isfinite(created) and 0 <= now - created <= ttl


def prepare_context(request, limit, now, ttl):
    """Keep the current turn intact and fit the newest complete turns before it.

    Account for model-visible tool arguments and results as well as prompts and
    schemas. Sizes describe this JSON representation, independent of billing.
    """
    messages = request.messages
    tools = [convert_to_openai_tool(tool) for tool in request.tools]
    system = request.system_message
    fixed = context_bytes({'system': system.content if system else None, 'tools': tools})
    # Framing includes each message type and tool-call association. Exclude usage
    # and response metadata, but retain provider-specific additional arguments.
    excluded = {'response_metadata', 'usage_metadata', 'artifact', 'id'}
    sizes = [context_bytes(message.model_dump(mode='json', exclude=excluded)) + 1 for message in messages]
    turns = [index for index, message in enumerate(messages) if isinstance(message, HumanMessage)]
    current = turns[-1] if turns else 0
    active = fixed + sum(sizes[current:])
    stats = {'limit_bytes': limit, 'before_bytes': fixed + sum(sizes), 'active_bytes': active}
    if active > limit:
        return None, stats

    remaining = limit - active
    kept = []
    expired = evidence = 0
    for start, end in reversed(list(zip(turns, turns[1:]))):
        last = messages[end - 1]
        if not isinstance(last, AIMessage) or last.tool_calls or last.invalid_tool_calls:
            continue
        turn = messages[start:end]
        if not fresh(turn[0].response_metadata.get(CONTEXT_TIMESTAMP), now, ttl):
            expired += 1
            continue
        # Keep prior evidence in the checkpoint. The next request must use its own tool results.
        if any(isinstance(message, ToolMessage) for message in turn):
            evidence += 1
            continue
        # Completed text-only turns can cross providers. Keep the active tool chain intact.
        turn = [type(message)(content=message.text) for message in turn]
        size = sum(context_bytes(message.model_dump(mode='json',exclude=excluded)) + 1 for message in turn)
        if size > remaining:
            break
        kept.append(turn)
        remaining -= size

    selected = [message for turn in reversed(kept) for message in turn] + messages[current:]
    stats.update(after_bytes=limit - remaining, omitted_messages=len(messages) - len(selected),
                 expired_turns=expired, evidence_turns=evidence)
    return selected, stats
