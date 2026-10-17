"""The embedder checks that something is listening before it asks, and a machine with no Ollama runs on keywords without
a word, a stall or a retry every few minutes. A service that is expected but not running is announced once."""
import logging

import pytest

from agent8088 import capabilities
from agent8088.memory import embed
from agent8088.memory.embed import Embedder


class Client:
    """An OpenAI-shaped client that counts the requests it is asked to send."""

    def __init__(self, base_url="http://127.0.0.1:11434/v1/", fail=False):
        self.base_url = base_url
        self.fail = fail
        self.requests = 0

    @property
    def embeddings(self):
        return self

    def create(self, model, input, timeout=None):
        self.requests += 1
        if self.fail:
            raise ConnectionError("connection refused")
        item = type("E", (), {"embedding": [1.0, 0.0]})()
        return type("R", (), {"data": [item for _ in input]})()


@pytest.fixture
def machine(monkeypatch):
    """What this machine looks like: is anything listening, is Ollama installed. Counts the probes."""
    state = {"listening": False, "installed": False, "probes": 0}

    def reachable(host, port, timeout=0.8):
        state["probes"] += 1
        return state["listening"]

    monkeypatch.setattr(embed, "_reachable", reachable)
    monkeypatch.setattr(embed, "_ollama_installed", lambda: state["installed"])
    return state


def _embedder(client, provider="ollama", **kwargs):
    return Embedder(lambda: client, "nomic-embed-text", provider=provider, **kwargs)


def test_no_ollama_at_all_is_keyword_only_and_quiet(machine, caplog):
    client = Client()
    embedder = _embedder(client)
    with caplog.at_level(logging.DEBUG):
        assert embedder.embed(["hello"]) == []
    assert client.requests == 0, "no request was sent to an address nothing listens on"
    entry = capabilities.get(capabilities.MEMORY_EMBED)
    assert entry.state == capabilities.OK and entry.active == "keyword-only (BM25)"
    assert "Ollama is not installed" in entry.reason
    assert not [r for r in caplog.records if getattr(r, "degraded", False)], "nothing announced"
    assert not capabilities.degraded()


def test_no_ollama_is_not_asked_again_for_a_long_time(machine, monkeypatch):
    client = Client()
    embedder = _embedder(client)
    embedder.embed(["a"])
    assert machine["probes"] == 1
    now = [embedder._failed_at]
    monkeypatch.setattr(embed.time, "time", lambda: now[0] + 3600)      # an hour later: still waiting
    assert not embedder.ready()
    assert embedder.embed(["b"]) == []
    assert machine["probes"] == 1
    now[0] += 3600 * 3                                                    # past the longest window
    assert embedder.ready()


def test_ollama_installed_but_not_running_is_announced_once(machine, caplog):
    machine["installed"] = True
    client = Client()
    embedder = _embedder(client, breaker_seconds=0)
    with caplog.at_level(logging.DEBUG):
        assert embedder.embed(["a"]) == []
        assert embedder.embed(["b"]) == []
    assert client.requests == 0
    entry = capabilities.get(capabilities.MEMORY_EMBED)
    assert entry.state == capabilities.DEGRADED
    assert "start Ollama" in entry.fix and "nomic-embed-text" in entry.fix
    assert len([r for r in caplog.records if getattr(r, "degraded", False)]) == 1


def test_a_remote_ollama_that_does_not_answer_is_not_called_absent(machine):
    machine["installed"] = False
    client = Client(base_url="http://gpu-box.lan:11434/v1/")
    embedder = _embedder(client)
    embedder.embed(["a"])
    assert capabilities.get(capabilities.MEMORY_EMBED).state == capabilities.DEGRADED


def test_another_provider_that_does_not_answer_is_announced(machine):
    client = Client(base_url="http://127.0.0.1:8000/v1/")
    embedder = _embedder(client, provider="custom")
    embedder.embed(["a"])
    assert capabilities.get(capabilities.MEMORY_EMBED).state == capabilities.DEGRADED


def test_a_listening_service_is_used_and_not_probed_again(machine):
    machine["listening"] = True
    client = Client()
    embedder = _embedder(client)
    assert embedder.embed(["a"]) == [[1.0, 0.0]]
    assert embedder.embed(["b"]) == [[1.0, 0.0]]
    assert machine["probes"] == 1
    assert capabilities.get(capabilities.MEMORY_EMBED).state == capabilities.OK


def test_a_listening_service_that_errors_still_degrades(machine):
    machine["listening"] = True
    client = Client(fail=True)
    embedder = _embedder(client)
    assert embedder.embed(["a"]) == []
    assert client.requests == 1
    assert capabilities.get(capabilities.MEMORY_EMBED).state == capabilities.DEGRADED


def test_a_service_that_comes_up_later_is_used(machine, monkeypatch):
    client = Client()
    embedder = _embedder(client, breaker_seconds=0)
    assert embedder.embed(["a"]) == []
    machine["listening"] = True
    assert embedder.embed(["b"]) == [[1.0, 0.0]]
    assert capabilities.get(capabilities.MEMORY_EMBED).state == capabilities.OK


def test_the_window_grows_with_repeated_failures_and_resets_on_success(machine, monkeypatch):
    machine["installed"] = True
    client = Client()
    embedder = _embedder(client, breaker_seconds=100)
    windows = []
    for _ in range(4):
        embedder._failed_at = 0.0
        embedder.embed(["a"])
        windows.append(embedder._window())
    assert windows == [100, 600, 2400, 2400]
    machine["listening"] = True
    embedder._failed_at = 0.0
    embedder.embed(["a"])
    assert embedder._failures == 0 and embedder._window() == 100


def test_a_client_with_no_address_is_asked_as_before(machine):
    class NoAddress:
        embeddings = None

        def __init__(self):
            self.requests = 0
            self.embeddings = self

        def create(self, model, input, timeout=None):
            self.requests += 1
            item = type("E", (), {"embedding": [1.0]})()
            return type("R", (), {"data": [item for _ in input]})()

    client = NoAddress()
    embedder = Embedder(lambda: client, "m", provider="ollama")
    assert embedder.embed(["x"]) == [[1.0]]
    assert machine["probes"] == 0 and client.requests == 1


def test_endpoint_state_reads_the_address(machine):
    assert embed.endpoint_state("not a url") == "unknown"
    assert embed.endpoint_state("http://127.0.0.1:11434/v1/", "ollama") == "absent"
    machine["installed"] = True
    assert embed.endpoint_state("http://127.0.0.1:11434/v1/", "ollama") == "down"
    machine["listening"] = True
    assert embed.endpoint_state("http://127.0.0.1:11434/v1/", "ollama") == "up"
