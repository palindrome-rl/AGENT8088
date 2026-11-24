"""/sandbox, /doctor and the capability registry must not claim a network control the backend lacks.

The Windows native backend restricts file writes only. Reporting its network as
"blocked" (or accepting sandbox_allowed_domains silently) tells the user a
control is active when a sandboxed command can still reach any host.
"""
import pytest

from agent8088 import capabilities, cli


@pytest.fixture
def status_for(engine, monkeypatch):
    def make(backend, enforces=True, domains=()):
        monkeypatch.setattr(engine, "_resolve_sandbox_backend", lambda: backend)
        monkeypatch.setattr(engine, "_native_enforces_network", lambda: enforces)
        monkeypatch.setattr(engine, "SANDBOX_ALLOWED_DOMAINS", list(domains))
        monkeypatch.setattr(engine, "_native_sandbox_missing_requirements", lambda: [])
        return engine.sandbox_status()
    return make


def test_windows_native_says_the_network_policy_is_not_enforced(status_for):
    status = status_for("native", enforces=False)
    assert status["network_enforced"] is False
    assert status["host_processes_shared"] is True
    assert "not enforced" in status["network"] and "write restriction only" in status["network"]
    assert status["network"] != "blocked"


def test_an_allowlist_on_that_backend_is_reported_as_not_applied(status_for):
    status = status_for("native", enforces=False, domains=["pypi.org"])
    assert "pypi.org" in status["network"] and "not applied" in status["network"]


def test_enforcing_native_backends_report_as_before(status_for):
    blocked = status_for("native", enforces=True)
    assert blocked["network_enforced"] is True and blocked["network"] == "blocked"
    assert blocked["host_processes_shared"] is False
    allowed = status_for("native", enforces=True, domains=["pypi.org", "github.com"])
    assert allowed["network"] == "pypi.org, github.com"


def test_docker_keeps_its_network_claim_on_any_os(status_for, engine, monkeypatch):
    monkeypatch.setattr(engine, "DOCKER_NETWORK_CONFIGURED", True)  # docker_network=none written down
    status = status_for("docker", enforces=False)
    assert status["network_enforced"] is True and status["network"] == "blocked"
    assert status["host_processes_shared"] is False


def test_no_backend_means_no_claim(status_for):
    assert status_for("unavailable")["network_enforced"] is None


def test_capability_registry_degrades_for_the_unenforced_backend(engine, status_for):
    engine._report_sandbox(status_for("native", enforces=False, domains=["pypi.org"]))
    entry = capabilities.get(capabilities.SANDBOX_NETWORK)
    assert entry.state == capabilities.DEGRADED
    assert "not enforced" in entry.reason
    assert "sandbox_allowed_domains is not applied" in entry.impact
    assert "sandbox_backend=docker" in entry.fix
    # The backend itself is still reported as healthy: a later switch to Docker
    # must remain a visible state change.
    assert capabilities.get(capabilities.SANDBOX).state == capabilities.OK


def test_capability_registry_is_ok_where_the_network_is_enforced(engine, status_for):
    engine._report_sandbox(status_for("native", enforces=True))
    assert capabilities.get(capabilities.SANDBOX_NETWORK).state == capabilities.OK
    assert capabilities.get(capabilities.SANDBOX).state == capabilities.OK


def test_switching_to_docker_clears_the_warning_and_is_still_announced(engine, status_for):
    engine._report_sandbox(status_for("native", enforces=False))
    changes = []
    capabilities.subscribe(changes.append)
    engine._report_sandbox(status_for("docker", enforces=False))
    assert capabilities.get(capabilities.SANDBOX_NETWORK) is None
    assert [(c.name, c.new_state) for c in changes] == [(capabilities.SANDBOX, capabilities.DEGRADED)]


def test_no_backend_leaves_no_network_entry(engine, status_for):
    engine._report_sandbox(status_for("native", enforces=False))
    engine._report_sandbox(status_for("unavailable"))
    assert capabilities.get(capabilities.SANDBOX_NETWORK) is None


def _sandbox_row(status):
    return next(c for c in cli._doctor_base_checks() if c["name"] == "Sandbox")


def test_doctor_row_warns_when_an_allowlist_is_set_but_not_applied(engine, status_for, monkeypatch):
    status_for("native", enforces=False, domains=["pypi.org"])
    row = _sandbox_row(None)
    assert row["status"] == "warn"
    assert "not enforced" in row["detail"] and "docker" in row["fix"]


def test_doctor_row_is_informational_without_an_allowlist(engine, status_for):
    status_for("native", enforces=False)
    row = _sandbox_row(None)
    assert row["status"] == "info" and "not enforced" in row["detail"]


def test_doctor_row_is_unchanged_where_enforced(engine, status_for):
    status_for("native", enforces=True)
    row = _sandbox_row(None)
    assert row["status"] == "info" and "not enforced" not in row["detail"]


@pytest.mark.parametrize("platform,expected", [("win32", False), ("linux", True), ("darwin", True)])
def test_only_the_windows_backend_lacks_network_enforcement(engine, monkeypatch, platform, expected):
    monkeypatch.setattr(engine.sys, "platform", platform)
    assert engine._native_enforces_network() is expected


def test_unfiltered_docker_network_is_not_claimed_as_enforced(status_for, engine, monkeypatch):
    monkeypatch.setattr(engine, "DOCKER_NETWORK", "bridge")
    status = status_for("docker")
    assert status["network_enforced"] is False
    assert "not filtered" in status["network"]
    assert status["host_processes_shared"] is False
