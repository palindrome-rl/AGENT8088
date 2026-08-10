"""Shared fixtures: load the agent8088 engine as a module."""
import importlib
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
# Test collection imports CLI and gateway modules before fixtures run. Keep those
# imports independent of a developer's real ~/.agent8088/config.txt.
os.environ["AGENT8088_CONFIG"] = str(ROOT / "_no_such_config.txt")


def _load_engine():
    os.environ["AGENT8088_CONFIG"] = str(ROOT / "_no_such_config.txt")
    os.environ["AGENT8088_SANDBOX"] = "local"
    sys.path.insert(0, str(ROOT / "src"))
    from agent8088 import engine as mod
    return importlib.reload(mod)


@pytest.fixture
def engine():
    """Fresh engine module per test (module globals are mutable in tests)."""
    return _load_engine()


@pytest.fixture
def register_tool(engine):
    """Register a throwaway tool spec on the engine under test.

    Needed because the shipped tool set no longer contains an http_post tool:
    web_search_tavily and web_search_exa were folded into the web_search
    provider registry, and get_page_title is the only remaining http tool.

    The http_post branch of run_tool's network gate and of _exec_http is still
    live code, so it still needs a tool to exercise it. A test-local spec keeps
    that coverage without shipping a vendor tool purely for the tests. Mutating
    the module globals is safe here because the `engine` fixture reloads the
    module for every test.
    """
    def _register(name, description="test tool", **fields):
        spec = engine._build_spec(name, fields, engine.APP_CONFIG, description)
        engine.TOOL_SPECS[name] = spec
        engine.TOOL_NAMES.add(name)
        engine.TOOL_REQUIRED_PARAMS[name] = list(spec["args"])
        return spec

    return _register


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
