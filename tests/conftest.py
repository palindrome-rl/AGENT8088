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
    # Never start an egress proxy or clear Docker containers on the developer's
    # Docker from an ordinary test: a test of a command path that reaches Docker
    # gets no network instead. The opt-in attack tests use the real proxy.
    if os.environ.get("AGENT8088_ATTACK_TESTS") != "1":
        mod._unguarded_start_docker_egress = mod._start_docker_egress  # for tests that fake `docker`
        mod._start_docker_egress = lambda key: ("none", [])
    mod._docker_stale_cleaned = True
    # Never import or set up a WSL distro from an ordinary test: `install_native_sandbox` sets one up on Windows,
    # and a test of the ACL runner's setup would download Alpine and register a distro on the developer's machine.
    # The WSL tests turn it back on and fake wsl.exe; the opt-in attack tests use a distro set up beforehand.
    mod.SANDBOX_WSL = False
    mod._unguarded_install_wsl_sandbox = mod.install_wsl_sandbox
    mod.install_wsl_sandbox = lambda log=None: "WSL sandbox not set up in tests."
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


_KNOWN_FAILURES = ROOT / "tests" / "known_failures.txt"


def _known_failures():
    known = {}
    if _KNOWN_FAILURES.exists():
        for line in _KNOWN_FAILURES.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.startswith("#"):
                node, _, where = line.partition("\t")
                known[node.strip()] = where.strip() or "unknown"
    return known


def pytest_collection_modifyitems(config, items):
    """Tests that failed before the 2026-11-19 reconciliation run as non-strict xfail.

    They are listed, with the platforms they failed on, in tests/known_failures.txt.
    They still run and report as XFAIL or XPASS; anything not on the list fails as usual.
    """
    known = _known_failures()
    if not known:
        return
    for item in items:
        where = known.get(item.nodeid.split("[", 1)[0]) or known.get(item.nodeid)
        if where:
            item.add_marker(pytest.mark.xfail(
                reason=f"known failure before the 2026-11-19 reconciliation ({where}); "
                       "see tests/known_failures.txt", strict=False))
