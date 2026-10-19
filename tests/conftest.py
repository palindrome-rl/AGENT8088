"""Shared fixtures: load the agent8088 engine as a module."""
import importlib
import os
os.environ["ANONYMIZED_TELEMETRY"] = "false"
os.environ["BROWSER_USE_CLOUD_SYNC"] = "false"
os.environ["POSTHOG_DISABLED"] = "1"
# Startup may launch Docker Desktop; a test run must never start the developer's
# Docker. Tests of the launcher set this back to 1.
os.environ["AGENT8088_DOCKER_AUTOSTART"] = "0"
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
def _no_real_searxng_container(monkeypatch):
    """Keep tests away from the developer's real `agent8088-searxng` container.

    The uninstall tests run `_run_windows_uninstall` and `_run_posix_uninstall`, which end in
    `searxng_provision.stop()`: a real `docker rm -f agent8088-searxng`. On a machine where
    `/search setup` had started that container, a full test run removed it, and the next real
    search found its local SearXNG gone. Every docker call the provisioner makes now answers
    "no such container" unless the test installs its own fake `_run`, as the provisioning tests do."""
    from types import SimpleNamespace

    from agent8088 import searxng_provision
    monkeypatch.setattr(
        searxng_provision, "_run",
        lambda argv, timeout=90: SimpleNamespace(
            returncode=1, stdout="", stderr="Error: No such object (blocked by tests/conftest.py)"))


@pytest.fixture(autouse=True)
def _no_real_install_state(monkeypatch):
    """The startup banner and /doctor read $AGENT8088_HOME/install-state.json.
    Point that at a file that never exists, so a real install's skipped stages
    cannot leak into unrelated tests (tests of the reader pass a file)."""
    from agent8088 import install_state
    monkeypatch.setattr(install_state, "path", lambda: ROOT / "_no_such_install_state.json")
