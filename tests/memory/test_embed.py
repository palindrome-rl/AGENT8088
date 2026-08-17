"""The embedder degrades rather than raising, and stops retrying a dead model.

Nothing here reaches a network. The client is a stub whose behaviour each test
states, which is the only way to assert on the failure paths at all.
"""
import pytest

from agent8088.memory.embed import Embedder


class StubEmbeddings:
    def __init__(self, owner):
        self.owner = owner

    def create(self, *, model, input):
        self.owner.calls.append((model, list(input)))
        if self.owner.error:
            raise self.owner.error
        count = self.owner.returns_count if self.owner.returns_count is not None else len(input)
        return type("Response", (), {
            "data": [type("Item", (), {"embedding": [0.1, 0.2, 0.3]})()
                     for _ in range(count)]
        })()


class StubClient:
    def __init__(self, error=None, returns_count=None):
        self.error = error
        self.returns_count = returns_count
        self.calls = []
        self.embeddings = StubEmbeddings(self)


def test_embedding_returns_one_vector_per_input():
    client = StubClient()
    embedder = Embedder(lambda: client, "nomic-embed-text")
    assert embedder.embed(["a", "b"]) == [[0.1, 0.2, 0.3], [0.1, 0.2, 0.3]]
    assert client.calls == [("nomic-embed-text", ["a", "b"])]


def test_blank_inputs_are_dropped_before_the_call():
    client = StubClient()
    embedder = Embedder(lambda: client, "nomic-embed-text")
    embedder.embed(["real", "  ", ""])
    assert client.calls == [("nomic-embed-text", ["real"])]


def test_an_all_blank_input_makes_no_call_at_all():
    client = StubClient()
    assert Embedder(lambda: client, "m").embed(["  "]) == []
    assert client.calls == []


def test_a_failing_embedder_returns_empty_rather_than_raising():
    """Recall must degrade to BM25, not break the turn."""
    embedder = Embedder(lambda: StubClient(error=RuntimeError("model not found")), "m")
    assert embedder.embed(["a"]) == []
    assert "model not found" in embedder.last_error


def test_a_failing_client_factory_returns_empty():
    def explode():
        raise RuntimeError("no provider configured")
    embedder = Embedder(explode, "m")
    assert embedder.embed(["a"]) == []
    assert "no provider" in embedder.last_error


def test_a_provider_without_an_embeddings_endpoint_says_so():
    """The litellm provider path hands back a config dict, not a client."""
    embedder = Embedder(lambda: {"api_mode": "litellm"}, "m")
    assert embedder.embed(["a"]) == []
    assert "embeddings" in embedder.last_error


def test_no_model_configured_makes_no_call():
    client = StubClient()
    assert Embedder(lambda: client, "").embed(["a"]) == []
    assert client.calls == []


def test_a_short_response_is_refused_rather_than_mispaired():
    """Fewer vectors than inputs would otherwise pair a fact with another fact's
    vector, which is silent corruption rather than a visible failure."""
    embedder = Embedder(lambda: StubClient(returns_count=1), "m")
    assert embedder.embed(["a", "b"]) == []
    assert "1 vectors for 2 inputs" in embedder.last_error


def test_a_dead_embedder_is_not_retried_on_every_call():
    client = StubClient(error=RuntimeError("nope"))
    embedder = Embedder(lambda: client, "m", breaker_seconds=300)
    for _ in range(5):
        embedder.embed(["a"])
    assert len(client.calls) == 1


def test_the_breaker_expires_so_a_fixed_embedder_recovers():
    client = StubClient(error=RuntimeError("nope"))
    embedder = Embedder(lambda: client, "m", breaker_seconds=0)
    embedder.embed(["a"])
    client.error = None
    assert embedder.embed(["a"]) == [[0.1, 0.2, 0.3]]


def test_availability_is_probed_once_and_cached():
    client = StubClient()
    embedder = Embedder(lambda: client, "m")
    assert embedder.available()
    assert embedder.available()
    assert len(client.calls) == 1


def test_availability_is_false_for_a_model_that_does_not_answer():
    embedder = Embedder(lambda: StubClient(error=RuntimeError("nope")), "m")
    assert not embedder.available()


def test_embed_one_returns_a_bare_vector():
    assert Embedder(lambda: StubClient(), "m").embed_one("a") == [0.1, 0.2, 0.3]


def test_embed_one_returns_empty_when_unavailable():
    assert Embedder(lambda: StubClient(error=RuntimeError("x")), "m").embed_one("a") == []


def test_the_dimension_is_reported_after_a_real_call():
    embedder = Embedder(lambda: StubClient(), "m")
    assert embedder.dim is None
    embedder.embed(["a"])
    assert embedder.dim == 3
