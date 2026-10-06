"""Turn model-backend exceptions and config mistakes into plain guidance.

One classifier, used wherever a model call can fail in front of a person: the
agent loop's final error answer, the retry status line, and (in the CLI/web
layers) the banner and /doctor. The goal is the same tone as
local_models._daemon_unreachable_message: say what broke in one sentence, then
the one thing to do about it -- never a raw SDK repr.

Deliberately free of engine imports, so it can be unit-tested with fake
exception objects and imported by any front end.
"""
from __future__ import annotations

import difflib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

DOCTOR_HINT = "Run /doctor to check your setup."


@dataclass(frozen=True)
class Friendly:
    """A classified model error.

    kind: connection | timeout | auth | model_not_found | endpoint_not_found |
          rate_limit | server_error | context_overflow | bad_request | unknown
    message: what happened, one sentence.
    fix: what to do about it ("" when there is nothing useful to say).
    retryable: whether trying the same request again can succeed.
    retry_after: seconds the server asked us to wait (429), when it said.
    """
    kind: str
    message: str
    fix: str
    retryable: bool
    retry_after: float | None = None

    @property
    def hint(self) -> str:
        # A context overflow is about the conversation, not the setup, and a
        # transient 5xx/429 is not fixed by checking anything.
        return "" if self.kind in ("context_overflow", "rate_limit", "server_error") else DOCTOR_HINT

    def render(self, *, hint: bool = True) -> str:
        parts = [self.message, self.fix, self.hint if hint else ""]
        return " ".join(part for part in parts if part)


# --- exception inspection ----------------------------------------------------

def _chain(exc):
    """exc and everything it was raised from, outermost first, cycle-safe."""
    seen = []
    while exc is not None and all(exc is not other for other in seen) and len(seen) < 8:
        seen.append(exc)
        exc = getattr(exc, "__cause__", None) or getattr(exc, "__context__", None)
    return seen


def status_code(exc) -> int | None:
    """HTTP status carried by an SDK/httpx/urllib error, if any."""
    for candidate in (getattr(exc, "status_code", None),
                      getattr(getattr(exc, "response", None), "status_code", None),
                      getattr(exc, "status", None),
                      getattr(exc, "code", None)):
        if isinstance(candidate, bool):
            continue
        try:
            value = int(candidate)
        except (TypeError, ValueError):
            continue
        if 100 <= value <= 599:
            return value
    return None


def _text(exc) -> str:
    """Lower-cased str(exc) plus any structured error body, for marker tests."""
    parts = [str(exc)]
    body = getattr(exc, "body", None)
    if body:
        try:
            parts.append(body if isinstance(body, str) else json.dumps(body, default=str))
        except (TypeError, ValueError):
            parts.append(str(body))
    code = getattr(exc, "code", None)
    if isinstance(code, str):
        parts.append(code)
    return " ".join(parts).lower()


def short_message(exc, limit: int = 240) -> str:
    """str(exc) collapsed to one line; the class name when str() is empty."""
    text = " ".join(str(exc).split()) or type(exc).__name__
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def retry_after_seconds(exc) -> float | None:
    """Retry-After in seconds (0 is a real answer, not "missing")."""
    headers = getattr(getattr(exc, "response", None), "headers", None)
    if headers is None:
        return None
    try:
        raw = headers.get("retry-after") or headers.get("Retry-After")
    except Exception:  # noqa: BLE001 -- a header object we don't understand
        return None
    if raw is None or str(raw).strip() == "":
        return None
    try:
        seconds = float(str(raw).strip())
        return max(0.0, seconds) if math.isfinite(seconds) else None
    except ValueError:
        pass
    try:
        import time
        from email.utils import parsedate_to_datetime
        return max(0.0, parsedate_to_datetime(str(raw)).timestamp() - time.time())
    except Exception:  # noqa: BLE001
        return None


_OVERFLOW_MARKERS = (
    "context_length_exceeded", "maximum context length", "context length exceeded",
    "prompt is too long", "too many tokens", "context window", "exceeds the context",
    "input is too long", "reduce the length of the messages", "max_model_len",
    "string_above_max_length", "request too large",
)


def is_context_overflow(exc) -> bool:
    """Whether the server rejected the request for being too long."""
    if exc is None:
        return False
    if status_code(exc) == 413:
        return True
    text = _text(exc)
    if any(marker in text for marker in _OVERFLOW_MARKERS):
        return True
    # vLLM: "This model's maximum context length is 32768 tokens. However, you
    # requested 40000 tokens" -- covered above; llama.cpp phrases it as
    # "the request exceeds the available context size".
    return "exceeds the available context" in text


_DNS_MARKERS = ("name or service not known", "nodename nor servname", "getaddrinfo failed",
                "temporary failure in name resolution", "failed to resolve", "no address associated",
                "name resolution", "gaierror")
_REFUSED_MARKERS = ("connection refused", "connectionrefused", "errno 61", "errno 111",
                    "actively refused", "connecterror", "all connection attempts failed",
                    "failed to establish a new connection", "cannot connect to host")


def is_unreachable(exc) -> bool:
    """Connection refused or DNS failure: the server is not there at all.

    Distinct from a dropped connection or a timeout -- retrying a refused
    connection a moment later almost never helps, so callers fail fast on it.
    """
    for item in _chain(exc):
        if isinstance(item, ConnectionRefusedError):
            return True
        name = type(item).__name__.lower()
        if name in ("gaierror", "connecterror"):
            return True
        text = str(item).lower()
        if any(marker in text for marker in _DNS_MARKERS + _REFUSED_MARKERS):
            return True
    return False


def _is_timeout(exc) -> bool:
    for item in _chain(exc):
        if isinstance(item, TimeoutError):
            return True
        name = type(item).__name__.lower()
        if "timeout" in name:
            return True
    text = _text(exc)
    return "timed out" in text or "timeout" in text


def _is_connection(exc) -> bool:
    if is_unreachable(exc):
        return True
    for item in _chain(exc):
        if isinstance(item, ConnectionError):
            return True
        name = type(item).__name__.lower()
        if "connection" in name or name in ("remoteprotocolerror", "readerror", "urlerror"):
            return True
    text = _text(exc)
    return "connection error" in text or "connection reset" in text or "server disconnected" in text


_MODEL_404 = re.compile(r"model\b.*\b(not found|does not exist|not exist|no such|unknown|is not available|not available)"
                        r"|model_not_found|no such model|unknown model|try pulling it")


def _is_ollama(provider, base_url) -> bool:
    provider = (provider or "").lower()
    if provider == "ollama-cloud":
        return False
    return provider == "ollama" or ":11434" in (base_url or "")


# --- classifier -------------------------------------------------------------

def explain_model_error(exc, *, provider=None, base_url=None, model=None,
                        api_key_env=None, timeout_seconds=None) -> Friendly:
    """Classify a model-call failure into a Friendly message + fix."""
    where = (base_url or "").strip() or "the model server"
    who = provider or "the provider"
    detail = short_message(exc)
    status = status_code(exc)
    text = _text(exc)
    ollama = _is_ollama(provider, base_url)

    if is_context_overflow(exc):
        window = f"provider.{provider}.context_window" if provider else "context_window"
        return Friendly(
            "context_overflow",
            "The conversation is too long for this model's context.",
            f"Run /compact or /reset, or raise {window} if the model supports more.",
            False)

    if status in (401, 403):
        key = api_key_env or "the provider's API key"
        return Friendly(
            "auth", f"API key rejected by {who} (HTTP {status}).",
            f"Check {key}, or run `agent8088 --setup`.", False)

    if status == 404:
        if _MODEL_404.search(text) or (model and model.lower() in text and "not found" in text):
            name = model or "that model"
            if ollama:
                fix = f"Install it: `ollama pull {name}` (or pick another with /models)."
            else:
                fix = "Pick another with /models."
            return Friendly("model_not_found", f"{who} doesn't have the model '{name}'.", fix, False)
        return Friendly(
            "endpoint_not_found", f"{where} answered 404: endpoint not found.",
            "base_url probably needs to end in /v1 (an OpenAI-compatible endpoint).", False)

    if status == 429 or "rate limit" in text or "rate_limit" in text:
        wait = retry_after_seconds(exc)
        message = f"Rate limited by {who}"
        message += f"; it asked to wait {wait:.0f}s." if wait is not None else "."
        return Friendly("rate_limit", message,
                        "Wait a moment and retry, or switch model with /model.", True, wait)

    if status is not None and status >= 500:
        return Friendly("server_error", f"{where} had a server error (HTTP {status}): {detail}",
                        "This is usually temporary; retry in a moment.", True)

    if _is_timeout(exc):
        span = f"{int(timeout_seconds)}s" if timeout_seconds else "time"
        return Friendly("timeout", f"{where} didn't answer in {span}.",
                        "Raise timeout_seconds in config.txt, or check the server isn't overloaded.",
                        True)

    if _is_connection(exc):
        if ollama:
            fix = "Start it: `ollama serve`, then retry."
        else:
            fix = "Check the server is running and base_url is right."
        return Friendly("connection", f"Can't reach {where}.", fix, not is_unreachable(exc))

    if status == 400:
        return Friendly("bad_request", f"{who} rejected the request: {detail}", "", False)

    return Friendly("unknown", detail, "", False)


# --- config key checks ------------------------------------------------------

# Fields load_providers / the engine read from provider.<name>.<field>.
PROVIDER_FIELDS = frozenset({
    "model", "api_mode", "base_url", "api_key", "api_key_env", "native_tools",
    "context_window", "max_completion_tokens", "extra_body", "reasoning_effort",
    "temperature", "vision", "label", "default_model",
})

# Dotted families whose suffix is a free-form name (a tool, sub-agent, skill).
_DOTTED_FAMILIES = ("provider", "tool_", "subagent_", "skill", "gateway", "mcp")

_KNOWN_KEYS_CACHE: frozenset | None = None
_LITERAL = re.compile(r"""["']([a-z][a-z0-9_]{1,63})(?:["']|\.\{)""")


def known_config_keys(package_dir: Path | None = None, extra_files=()) -> frozenset:
    """Every key the code could read, derived from the source itself.

    A static list rots the day someone adds a setting; this reads every
    identifier-shaped string literal in the package (APP_CONFIG.get("x"),
    config.get(f"x.{name}") ...) plus the keys named in the shipped config.txt,
    commented-out examples included. Over-inclusive on purpose: a missed typo
    costs nothing, a false "unknown key" warning teaches people to ignore them.
    Empty when no sources are present (a byte-compiled install), which turns
    the check off rather than flagging everything.
    """
    global _KNOWN_KEYS_CACHE
    if _KNOWN_KEYS_CACHE is not None and package_dir is None and not extra_files:
        return _KNOWN_KEYS_CACHE
    root = Path(package_dir) if package_dir else Path(__file__).resolve().parent
    keys = set()
    for path in root.rglob("*.py"):
        try:
            keys.update(_LITERAL.findall(path.read_text(encoding="utf-8", errors="replace")))
        except OSError:
            continue
    for path in (root / "config.txt", *extra_files):
        try:
            for line in Path(path).read_text(encoding="utf-8-sig", errors="replace").splitlines():
                line = line.strip().lstrip("#").strip()
                if "=" in line:
                    key = line.split("=", 1)[0].strip()
                    if re.fullmatch(r"[a-z][a-z0-9_.]*", key):
                        keys.add(key)
        except OSError:
            continue
    result = frozenset(keys)
    if package_dir is None and not extra_files:
        _KNOWN_KEYS_CACHE = result
    return result


def _suggest(word, candidates) -> str:
    match = difflib.get_close_matches(word, list(candidates), n=1, cutoff=0.75)
    return f" (did you mean '{match[0]}'?)" if match else ""


def unknown_config_keys(config: dict, known: frozenset | None = None,
                        source: str = "config.txt") -> list[str]:
    """Warnings for keys nothing reads and provider fields nothing uses."""
    known = known_config_keys() if known is None else known
    if not known:
        return []
    warnings = []
    plain = [key for key in known if "." not in key]
    for key in config:
        key = str(key)
        if key.startswith("provider."):
            parts = key.split(".", 2)
            if len(parts) == 3 and parts[2] not in PROVIDER_FIELDS:
                warnings.append(f"{source}: unknown provider field '{key}'"
                                f"{_suggest(parts[2], PROVIDER_FIELDS)}; it is ignored.")
            continue
        head = key.split(".", 1)[0]
        if key in known or head in known:
            continue
        if "." in key and head.startswith(_DOTTED_FAMILIES):
            continue
        warnings.append(f"{source}: unknown setting '{key}'{_suggest(key, plain)}; it is ignored.")
    return warnings


def suggest_name(word: str, candidates) -> str:
    """Public " (did you mean 'x'?)" helper for provider/model typos."""
    return _suggest(word, candidates)
