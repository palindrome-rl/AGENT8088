"""Shared fixtures: load the extension-less `agent8088` engine as a module."""
import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load_engine():
    loader = SourceFileLoader("agent8088_core", str(ROOT / "agent8088"))
    spec = importlib.util.spec_from_loader("agent8088_core", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


@pytest.fixture
def engine():
    """Fresh engine module per test (module globals are mutable in tests)."""
    return _load_engine()


class ScriptedModel:
    """Stand-in for create_completion: returns queued responses in order.

    Each queued item is the raw assistant `content` string. run_agent stops
    when a response contains no parseable tool call, so end your script with a
    plain-text 'final answer' string.
    """
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def __call__(self, client, messages, tools, **kwargs):
        self.calls.append({"messages": list(messages), "kwargs": kwargs})
        content = self._responses.pop(0) if self._responses else "done"
        return type("R", (), {"choices": [type("C", (), {
            "message": type("M", (), {"content": content}),
            "finish_reason": "stop",
        })()]})
