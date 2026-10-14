"""Shared fixtures: load the agent8088 engine as a module."""
import importlib
import os
os.environ["ANONYMIZED_TELEMETRY"] = "false"
os.environ["BROWSER_USE_CLOUD_SYNC"] = "false"
os.environ["POSTHOG_DISABLED"] = "1"
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load_engine():
    os.environ["AGENT8088_CONFIG"] = str(ROOT / "_no_such_config.txt")
    os.environ["AGENT8088_SANDBOX"] = "local"
    sys.path.insert(0, str(ROOT / "src"))
    from agent8088 import engine as mod
    mod = importlib.reload(mod)
    # Tests that overwrite files must not drop backups in the developer's real data
    # directory; the backup tests switch this back on and point it at a tmp path.
    mod.FILE_BACKUPS = False
    return mod


@pytest.fixture
def engine():
    """Fresh engine module per test (module globals are mutable in tests)."""
    return _load_engine()


@pytest.fixture(autouse=True)
def _fresh_capability_registry():
    """The degradation registry is process-global and, unlike engine, never
    reloaded: without this one test's "search degraded" leaks into the next
    test's /doctor or banner output."""
    from agent8088 import capabilities
    capabilities.reset()
    yield
    capabilities.reset()


@pytest.fixture(autouse=True)
def _no_real_install_state(monkeypatch):
    """The startup banner and /doctor read $AGENT8088_HOME/install-state.json.
    Point that at a file that never exists, so a real install's skipped stages
    cannot leak into unrelated tests (tests of the reader pass a file)."""
    from agent8088 import install_state
    monkeypatch.setattr(install_state, "path", lambda: ROOT / "_no_such_install_state.json")
