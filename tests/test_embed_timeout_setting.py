"""Embedding requests get 30 s by default and obey memory_embed_timeout_seconds.

Issue sheet row 49: a fixed 5 s limit failed every tool-index request on a
CPU-only Ollama, so tool selection fell back to keyword-only for the session.
"""
from types import SimpleNamespace as NS

import pytest

from agent8088 import memory
from agent8088.memory.embed import Embedder


class _Client:
    def __init__(self, seen):
        self.embeddings = self
        self.base_url = "https://embeddings.example/v1"
        self._seen = seen

    def create(self, *, model, input, timeout):
        self._seen.append(timeout)
        return NS(data=[NS(embedding=[1.0, 0.0]) for _ in input])


def _embed(timeout=None):
    seen = []
    kwargs = {} if timeout is None else {"timeout": timeout}
    vectors = Embedder(lambda: _Client(seen), "nomic-embed-text", provider="custom",
                       **kwargs).embed(["a", "b"])
    assert len(vectors) == 2
    return seen


def test_default_request_allows_thirty_seconds():
    assert _embed() == [30]


def test_the_limit_can_be_set():
    assert _embed(timeout=90) == [90]


@pytest.mark.parametrize("value, expected", [("45", 45), ("0", 1)])
def test_configure_passes_the_setting_to_the_embedder(tmp_path, value, expected):
    memory.configure(config={"memory_embed_timeout_seconds": value},
                     client_factory=lambda: None, db_path=str(tmp_path / "m.db"))
    try:
        assert memory.embedder().timeout == expected
    finally:
        memory.configure(config={}, client_factory=lambda: None,
                         db_path=str(tmp_path / "m.db"))
