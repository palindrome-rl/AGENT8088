"""Distil durable facts from a finished exchange.

One model call, ADD-only, given the exchange plus the memories already held on
the topic so it can skip what is known. The reply must be strict JSON; a reply
that is not parseable writes nothing rather than storing a guess.

What reaches this module has already been narrowed to the human's own words and
the agent's final answer. Tool output never arrives here -- that boundary is
enforced by the caller (memory/__init__.py) using the engine's own
_genuine_user_turns, and it is what stops a web page from writing the agent's
memory.
"""

import json
import logging
import re

log = logging.getLogger("agent8088.memory")

# Cap per turn. One pathological exchange should not be able to flood the store,
# and a model that starts enumerating trivia is a bug, not a windfall.
DEFAULT_MAX_PER_TURN = 10

# Longest single memory. A memory is a fact, not a transcript; anything longer is
# a summary that will never match a short query well anyway.
MAX_MEMORY_CHARS = 400

# Below this, an exchange is not worth a model call ("ls", "thanks", "yes").
MIN_EXCHANGE_CHARS = 40

PROMPT = """You extract durable facts from a conversation for long-term memory.

Return ONLY a JSON object, no prose, no code fence:
{"memories": [{"text": "...", "categories": ["..."]}]}

What qualifies as a memory:
- Who the user is: name, role, employer, team, location, timezone, languages,
  working hours, anything they state about themselves or their situation
- Stable preferences and conventions ("prefers uv over pip", "no emoji in commits")
- Decisions and constraints that outlive this exchange
- Facts about their projects, tools and environment
- Corrections the user made to you, and instructions meant to persist

What does NOT qualify:
- Transient state: what a command printed, what a file currently contains right now
- Restating the request, or your own plan for it
- Anything already covered by the memories shown below

Rules:
- Each memory is one self-contained sentence, understandable a year from now with
  no access to this conversation. No "it", "that file", "as discussed".
- Write it as a third-person fact, not as an instruction to yourself.
- When in doubt, extract. A slightly redundant memory costs far less than a
  missing one, and duplicates are filtered out after you.
- If the user says something about themselves, that is a memory. If they say it
  twice, it matters to them.
- At most {max_memories}. Return {"memories": []} only when the exchange genuinely
  contains nothing about the user, their work or their preferences.
- Never record credentials, tokens, keys or passwords, even if they appear above.

Memories already held (do not repeat or rephrase these):
{existing}

The exchange:
{exchange}
"""


def _format_existing(memories) -> str:
    if not memories:
        return "(none yet)"
    return "\n".join(f"- {text}" for text in memories)


def build_prompt(exchange, existing, *, max_memories=DEFAULT_MAX_PER_TURN) -> str:
    # str.format would trip over the JSON braces in the template, so the three
    # placeholders are substituted directly.
    return (PROMPT
            .replace("{max_memories}", str(max_memories))
            .replace("{existing}", _format_existing(existing))
            .replace("{exchange}", exchange))


def format_exchange(user_turns, answer) -> str:
    """Render the trusted part of a turn for the extraction prompt."""
    lines = [f"User: {text.strip()}" for text in user_turns if str(text).strip()]
    if str(answer or "").strip():
        lines.append(f"Assistant: {str(answer).strip()}")
    return "\n\n".join(lines)


def worth_extracting(exchange) -> bool:
    return len(str(exchange or "").strip()) >= MIN_EXCHANGE_CHARS


_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$")


def parse_response(raw, *, max_memories=DEFAULT_MAX_PER_TURN):
    """Memories from a model reply. [] for anything not strictly parseable.

    Models wrap JSON in a fence and prepend a sentence often enough that both are
    tolerated, but only structurally: a fence is stripped and the outermost object
    is located. Beyond that there is no repair and no free-text fallback -- an
    unparseable reply means this turn stores nothing, which is strictly better
    than storing a mangled fact that will be recalled as truth for months.
    """
    text = _FENCE.sub("", str(raw or "").strip())
    if not text:
        return []
    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            log.debug("memory extraction reply was not JSON; storing nothing")
            return []
        try:
            payload = json.loads(text[start:end + 1])
        except (ValueError, TypeError):
            log.debug("memory extraction reply was not JSON; storing nothing")
            return []

    if not isinstance(payload, dict):
        return []
    items = payload.get("memories")
    if not isinstance(items, list):
        return []

    out, seen = [], set()
    for item in items:
        if isinstance(item, str):
            item = {"text": item}
        if not isinstance(item, dict):
            continue
        text_value = " ".join(str(item.get("text") or "").split())[:MAX_MEMORY_CHARS]
        if not text_value:
            continue
        key = text_value.casefold()
        if key in seen:
            continue
        seen.add(key)
        raw_categories = item.get("categories")
        categories = [str(category)[:40] for category in raw_categories[:5]] \
            if isinstance(raw_categories, list) else []
        out.append({"text": text_value, "categories": categories})
        if len(out) >= max_memories:
            break
    return out
