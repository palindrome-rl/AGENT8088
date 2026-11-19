"""`/local pull` progress -- pull_model_stream yields each NDJSON event so the
CLI can render a progress bar from the completed/total byte counts."""
import json

import pytest

from agent8088 import local_models as L

_EVENTS = [
    {"status": "pulling manifest"},
    {"status": "pulling abc123", "completed": 100, "total": 400},
    {"status": "pulling abc123", "completed": 400, "total": 400},
    {"status": "success"},
]


class _FakeResponse:
    def __init__(self, lines):
        self._lines = [json.dumps(e).encode() + b"\n" for e in lines]

    def __iter__(self):
        return iter(self._lines)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_pull_model_stream_yields_events_with_progress(monkeypatch):
    monkeypatch.setattr(
        L.urllib.request, "urlopen",
        lambda req, timeout=None: _FakeResponse(_EVENTS))
    events = list(L.pull_model_stream("qwen3:0.6b"))
    assert [e["status"] for e in events] == [
        "pulling manifest", "pulling abc123", "pulling abc123", "success"]
    assert events[1]["completed"] == 100
    assert events[1]["total"] == 400


def test_pull_model_stream_raises_on_error_event(monkeypatch):
    monkeypatch.setattr(
        L.urllib.request, "urlopen",
        lambda req, timeout=None: _FakeResponse([{"error": "model not found"}]))
    with pytest.raises(L.OllamaError, match="model not found"):
        list(L.pull_model_stream("nope:latest"))
