"""Embeddings for the vector leg, via the OpenAI-compatible /embeddings endpoint.

No new dependency: Ollama's /v1/embeddings speaks the same protocol as everyone
else, so the `openai` client the engine already builds serves this too.

Nothing here raises at the caller. An embedder that is missing, unreachable or
serving a model that does not exist degrades recall to BM25-only, which is a
worse search but a working turn. Since a missing model fails the same way on
every call, failures trip a breaker instead of costing a round trip per turn.
"""

import logging
import os
import shutil
import socket
import threading
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import getproxies, proxy_bypass

from agent8088 import capabilities

log = logging.getLogger("agent8088.memory")

# Retry an embedder that failed only this often. A model that is not pulled is
# the common case and it fails identically every time; without this, every turn
# pays a connection attempt to learn the same thing.
_BREAKER_SECONDS = 300

# A service that keeps failing is asked less and less often: the first retry waits the breaker window, the
# next six times as long, then twenty-four times (300 s, 30 min, 2 h by default). A success starts over.
_BACKOFF = (1, 6, 24)

# How long to wait for a connection before deciding nothing is listening. A closed port answers at once; this only
# bounds a host that drops packets, which used to cost the full request timeout on every attempt.
_PROBE_SECONDS = 0.8
# One embeddings request. A CPU-only Ollama needs ~20 s for the tool index (one
# batch of every tool description); 5 s failed it every time and left tool
# selection keyword-only. A dead endpoint never waits this long: the port probe
# answers first. memory_embed_timeout_seconds overrides it.
_REQUEST_SECONDS = 30

_LOOPBACK = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def _endpoint(base_url):
    """(host, port) of an OpenAI-compatible base URL, or None when it cannot be read."""
    try:
        parsed = urlparse(str(base_url))
        host = parsed.hostname
        if not host:
            return None
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        return host, port
    except ValueError:
        return None


def _reachable(host, port, timeout=_PROBE_SECONDS):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _ollama_installed():
    """Whether Ollama is on this machine at all (a binary, its model folder, or its app)."""
    if shutil.which("ollama"):
        return True
    candidates = [Path.home() / ".ollama", Path("/usr/local/bin/ollama"), Path("/usr/bin/ollama"),
                  Path("/Applications/Ollama.app")]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "Programs" / "Ollama" / "ollama.exe")
    return any(candidate.exists() for candidate in candidates)


def endpoint_state(base_url, provider=""):
    """Whether something answers where embeddings would be asked for.

    "up": a connection opens. "absent": nothing answers and, for the built-in local ollama provider, Ollama is
    not installed here at all, so no message could tell the user to do something that helps. "down": nothing
    answers but local Ollama is installed and expected.
    "unknown": a direct probe is not applicable, so use the configured client's transport.
    """
    endpoint = _endpoint(base_url)
    if endpoint is None:
        return "unknown"
    host, port = endpoint
    # A direct TCP probe cannot judge a remote/proxied/custom client. Corporate
    # proxies and custom HTTP transports may work even when direct access does not.
    if str(provider or "").lower() != "ollama" or host.lower() not in _LOOPBACK:
        return "unknown"
    scheme = urlparse(str(base_url)).scheme
    proxies = getproxies()
    if (proxies.get(scheme) or proxies.get("all")) and not proxy_bypass(host):
        return "unknown"
    if _reachable(host, port):
        return "up"
    if str(provider or "").lower() == "ollama" and host.lower() in _LOOPBACK and not _ollama_installed():
        return "absent"
    return "down"


class Embedder:
    """Wraps one embedding model. Construct freely -- the probe is cached."""

    def __init__(self, client_factory, model, *, breaker_seconds=_BREAKER_SECONDS, provider="",
                 timeout=None):
        self._client_factory = client_factory
        self.timeout = timeout or _REQUEST_SECONDS
        self.model = model or ""
        self.provider = provider or ""
        self._breaker_seconds = breaker_seconds
        self._failures = 0          # failures in a row, for the back-off
        self._absent = False        # nothing here could serve embeddings: wait the longest window
        self._failed_at = 0.0
        self._last_error = ""
        self._probed = None
        self._dim = None
        self._lock = threading.Lock()

    # -- state -------------------------------------------------------------

    @property
    def last_error(self) -> str:
        return self._last_error

    @property
    def absent(self) -> bool:
        """True when the last attempt found no embedding service and none is expected on this machine."""
        return self._absent

    def _window(self) -> float:
        steps = len(_BACKOFF)
        multiplier = _BACKOFF[-1] if self._absent else _BACKOFF[min(max(self._failures, 1), steps) - 1]
        return self._breaker_seconds * multiplier

    def _blocked(self) -> bool:
        return bool(self._failed_at) and (time.time() - self._failed_at) < self._window()

    def _note_failure(self, exc) -> None:
        self._failed_at = time.time()
        self._failures += 1
        self._absent = False
        self._last_error = str(exc)[:200]
        self._probed = False
        log.debug("memory embedder unavailable: %s", self._last_error)
        self._report(False)

    def _note_unreachable(self, state, endpoint) -> None:
        """Nothing answers at the embeddings address. No request was sent, so this costs no timeout."""
        self._failed_at = time.time()
        self._failures += 1
        self._absent = state == "absent"
        where = f"{endpoint[0]}:{endpoint[1]}" if endpoint else "the embeddings address"
        self._last_error = f"no embedding service is listening at {where}"
        self._probed = False
        log.debug("memory embedder unavailable: %s", self._last_error)
        if self._absent:
            self._report_absent()
        else:
            self._report(False)

    def _note_success(self) -> None:
        self._failed_at = 0.0
        self._failures = 0
        self._absent = False
        self._last_error = ""
        self._probed = True
        self._report(True)

    def _report_absent(self) -> None:
        """No embedder on this machine: keyword recall is the normal mode here, not a fault to announce."""
        try:
            capabilities.report(
                capabilities.MEMORY_EMBED, active="keyword-only (BM25)", preferred="keyword-only (BM25)",
                state=capabilities.OK, reason="no embedding service found (Ollama is not installed)",
                impact="semantic matches are not used; recall and tool selection use keywords",
                fix=(f"optional: install Ollama and pull {self.model}, or set memory_embed_provider")
                if self.model else "")
        except Exception:  # noqa: BLE001 — reporting must never break recall
            pass

    def _report(self, ok: bool) -> None:
        """capabilities.MEMORY_EMBED: recall is keyword-only while this fails."""
        try:
            if ok:
                capabilities.report(capabilities.MEMORY_EMBED, active=self.model,
                                    preferred=self.model, state=capabilities.OK)
                return
            capabilities.report(
                capabilities.MEMORY_EMBED, active="keyword-only (BM25)", preferred=self.model,
                state=capabilities.DEGRADED,
                reason=f"embedder {self.model} failed: {self._last_error[:120]}",
                impact="keyword-only recall and tool selection; semantic matches missed",
                fix=(f"start Ollama and run ollama pull {self.model} (if Ollama serves it), or agent8088 --memory-setup"
                     if self.model else "agent8088 --memory-setup"))
        except Exception:  # noqa: BLE001 — reporting must never break recall
            pass

    def ready(self) -> bool:
        """Worth calling: has a model and the failure breaker is not open."""
        return bool(self.model) and not self._blocked()

    def available(self) -> bool:
        """Whether the model answers. Probed once with a trivial input, then
        cached -- a wrong model name should cost one round trip, not one per turn.
        """
        if self._probed is not None and not self._blocked():
            return bool(self._probed)
        return bool(self.embed(["ok"]))

    # -- work --------------------------------------------------------------

    def embed(self, texts):
        """Vectors for `texts`, or [] if the embedder cannot serve.

        [] is a real answer here, not an error: the caller drops the vector leg
        and ranks on BM25 alone.
        """
        cleaned = [str(text).strip() for text in texts if str(text).strip()]
        if not cleaned or not self.model:
            return []
        with self._lock:
            if self._blocked():
                return []
            client = None
            try:
                client = self._client_factory()
            except Exception as exc:
                self._note_failure(exc)
                return []
            # The litellm provider path hands back a config dict, not a client
            # with .embeddings. Rather than guess at a second protocol, say so
            # and let recall run on BM25.
            if not hasattr(client, "embeddings"):
                self._note_failure(RuntimeError(
                    "active provider exposes no /embeddings endpoint; "
                    "set memory_embed_provider to one that does"))
                return []
            # Probe only the built-in local Ollama path before its first success.
            # Remote/custom/proxied services must be judged by their configured transport.
            if self._probed is not True:
                address = getattr(client, "base_url", None)
                state = endpoint_state(address, self.provider)
                if state in ("down", "absent"):
                    self._note_unreachable(state, _endpoint(address))
                    return []
            try:
                # Embeddings are a best-effort recall aid. A missing local
                # Ollama service must not hold a turn indefinitely.
                response = client.embeddings.create(model=self.model, input=cleaned,
                                                    timeout=self.timeout)
                vectors = [list(item.embedding) for item in response.data]
            except Exception as exc:
                self._note_failure(exc)
                return []
            # A provider that silently returns fewer vectors than inputs would
            # otherwise pair text with the wrong vector further up.
            if len(vectors) != len(cleaned) or not all(vectors):
                self._note_failure(RuntimeError(
                    f"embedder returned {len(vectors)} vectors for {len(cleaned)} inputs"))
                return []
            self._dim = len(vectors[0])
            self._note_success()
            return vectors

    def embed_one(self, text):
        vectors = self.embed([text])
        return vectors[0] if vectors else []

    @property
    def dim(self):
        """Dimension of this model's output, or None if never seen. Probing here
        would hide a network call behind an attribute, so it only reports what a
        real call already revealed."""
        return getattr(self, "_dim", None)
