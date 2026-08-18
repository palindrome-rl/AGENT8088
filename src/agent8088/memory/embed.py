"""Embeddings for the vector leg, via the OpenAI-compatible /embeddings endpoint.

No new dependency: Ollama's /v1/embeddings speaks the same protocol as everyone
else, so the `openai` client the engine already builds serves this too.

Nothing here raises at the caller. An embedder that is missing, unreachable or
serving a model that does not exist degrades recall to BM25-only, which is a
worse search but a working turn. Since a missing model fails the same way on
every call, failures trip a breaker instead of costing a round trip per turn.
"""

import logging
import threading
import time

log = logging.getLogger("agent8088.memory")

# Retry an embedder that failed only this often. A model that is not pulled is
# the common case and it fails identically every time; without this, every turn
# pays a connection attempt to learn the same thing.
_BREAKER_SECONDS = 300


class Embedder:
    """Wraps one embedding model. Construct freely -- the probe is cached."""

    def __init__(self, client_factory, model, *, breaker_seconds=_BREAKER_SECONDS):
        self._client_factory = client_factory
        self.model = model or ""
        self._breaker_seconds = breaker_seconds
        self._failed_at = 0.0
        self._last_error = ""
        self._probed = None
        self._dim = None
        self._lock = threading.Lock()

    # -- state -------------------------------------------------------------

    @property
    def last_error(self) -> str:
        return self._last_error

    def _blocked(self) -> bool:
        return bool(self._failed_at) and (time.time() - self._failed_at) < self._breaker_seconds

    def _note_failure(self, exc) -> None:
        self._failed_at = time.time()
        self._last_error = str(exc)[:200]
        self._probed = False
        log.debug("memory embedder unavailable: %s", self._last_error)

    def _note_success(self) -> None:
        self._failed_at = 0.0
        self._last_error = ""
        self._probed = True

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
            try:
                response = client.embeddings.create(model=self.model, input=cleaned)
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
