"""Fixtures for the memory suite.

Every test gets its own database under tmp_path. The real
~/.agent8088/memory.db is never opened: nothing here reads AGENT8088_HOME
without the fixture setting it first, and no test constructs a default path.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "memory.db"


@pytest.fixture
def store(db_path):
    from agent8088.memory.store import MemoryStore
    with MemoryStore(db_path) as opened:
        yield opened


@pytest.fixture
def memory(db_path):
    """The package with clean runtime state, torn down after each test."""
    from agent8088 import memory as mod
    mod.reset()
    yield mod
    mod.reset()


class FakeEmbedder:
    """Deterministic vectors with no model call.

    Each text maps to a fixed vector supplied by the test, so similarity is
    something the test states outright rather than something a model decides.
    """

    def __init__(self, vectors=None, dim=4, fail=False):
        self.vectors = vectors or {}
        self.dim = dim
        self.fail = fail
        self.calls = []
        self.last_error = ""
        self.model = "fake-embed"

    def _vector(self, text):
        if text in self.vectors:
            return list(self.vectors[text])
        # Deterministic but arbitrary: unlisted texts get a stable vector that is
        # near-orthogonal to the listed ones, so they neither match nor crash.
        seed = sum(ord(character) for character in text)
        return [((seed >> shift) % 7) / 7.0 for shift in range(self.dim)]

    def embed(self, texts):
        self.calls.append(list(texts))
        if self.fail:
            return []
        return [self._vector(text) for text in texts]

    def embed_one(self, text):
        vectors = self.embed([text])
        return vectors[0] if vectors else []

    def available(self):
        return not self.fail


@pytest.fixture
def fake_embedder():
    return FakeEmbedder
