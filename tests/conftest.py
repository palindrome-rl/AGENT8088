"""Shared fixtures: load the agent8088 engine as a module."""
import importlib
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load_engine():
    os.environ["AGENT8088_CONFIG"] = str(ROOT / "_no_such_config.txt")
    sys.path.insert(0, str(ROOT / "src"))
    from agent8088 import engine as mod
    return importlib.reload(mod)


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


@pytest.fixture
def scripted():
    """Factory for ScriptedModel instances."""
    return ScriptedModel
