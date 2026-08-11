"""Shared fixtures: load the agent8088 engine as a module."""
import importlib
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest


def assert_owner_only(path):
    """Assert `path` is readable and writable by its owner and nobody else.

    POSIX states this as mode 0600. Windows cannot: NTFS ACLs do not map onto
    mode bits, and `st_mode` reads 0o666 on a correctly locked-down file — which
    is why asserting 0600 everywhere reports a false failure on Windows rather
    than a real one. The equivalent Windows claim is that the ACL names exactly
    one principal, the current user: `_protect_private_file` grants that SID and
    strips inheritance, so SYSTEM, Administrators and OWNER RIGHTS are removed.
    """
    if sys.platform != "win32":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        return
    listing = subprocess.run(["icacls", str(path)], capture_output=True,
                             text=True, timeout=20).stdout
    entries = [line for line in listing.splitlines() if ":(" in line]
    assert len(entries) == 1, f"expected exactly one ACL entry, got: {entries}"
    user = os.environ.get("USERNAME", "")
    assert user and user.lower() in entries[0].lower(), (
        f"sole ACL entry should belong to {user!r}: {entries[0]!r}")

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
def docker_image_present(engine):
    """Treat the default sandbox image as already pulled.

    `_exec_docker_command` probes for the image before starting a container, so a
    test that stubs `_exec_process` and inspects the argv would otherwise capture
    the probe rather than the container run. Provisioning has its own coverage in
    test_docker_image_pull.py; these tests are about what gets run, not whether
    the image is local.
    """
    engine._docker_images_present.add(engine.DOCKER_IMAGE)
    return engine


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


@pytest.fixture
def artifacts_dir():
    """Scratch directory for files a test needs to create.

    Tests that write into the repo root leave droppings that show up in every
    later `git status` and occasionally get committed by accident. Everything
    generated goes here instead; artifacts/ is gitignored.
    """
    path = ROOT / "artifacts" / "tests"
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture(scope="session", autouse=True)
def _repo_root_stays_clean():
    """Fail the run if a test created a file in the repo root.

    A guard rather than a convention: the rule is only worth having if
    breaking it is noisy, and "don't write to the repo root" is exactly the
    kind of thing that regresses silently.
    """
    ignore = {".pytest_cache", "__pycache__", ".ruff_cache", "artifacts",
              ".coverage", ".venv"}
    before = set(os.listdir(ROOT))
    yield
    created = set(os.listdir(ROOT)) - before - ignore
    assert not created, f"tests created files in the repo root: {sorted(created)}"
