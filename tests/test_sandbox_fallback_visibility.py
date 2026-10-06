"""The sandbox backend is reported to the capabilities registry when it is
settled and when it switches mid-session, and commands that ran in Docker tell
the model the network is off. No real sandbox, Docker or subprocess runs."""

import pytest

from agent8088 import capabilities


@pytest.fixture
def sandbox(engine, monkeypatch):
    state = {"native_missing": [], "docker": True}
    monkeypatch.setattr(engine, "SANDBOX_BACKEND", "auto")
    monkeypatch.setattr(engine, "_native_sandbox_broken", False)
    monkeypatch.setattr(engine, "_docker_sandbox_broken", False)
    monkeypatch.setattr(engine, "_native_sandbox_missing_requirements",
                        lambda: list(state["native_missing"]))
    monkeypatch.setattr(engine, "_docker_available", lambda: state["docker"])
    monkeypatch.setattr(engine, "_native_sandbox_ready", lambda *a, **k: not engine._native_sandbox_broken)
    monkeypatch.setattr(engine, "_docker_sandbox_ready", lambda *a, **k: state["docker"])
    return engine, state


def test_native_is_ok(sandbox):
    engine, _ = sandbox
    engine.verify_sandbox_backend()
    entry = capabilities.get(capabilities.SANDBOX)
    assert entry.state == capabilities.OK and entry.active == "native"
    assert engine._sandbox_model_note() == ""


def test_docker_fallback_at_startup_is_degraded(sandbox):
    engine, state = sandbox
    state["native_missing"] = ["sandbox-runtime"]
    engine.verify_sandbox_backend()
    entry = capabilities.get(capabilities.SANDBOX)
    assert entry.state == capabilities.DEGRADED
    assert entry.active == "docker" and entry.preferred == "native"
    assert "no network" in entry.impact
    assert "/sandbox setup" in entry.fix
    assert "no network" in engine._sandbox_model_note()


def test_mid_session_switch_to_docker_is_reported(sandbox):
    engine, _ = sandbox
    engine.verify_sandbox_backend()
    changes = []
    capabilities.subscribe(changes.append)
    engine._mark_native_sandbox_broken("sandbox-exec: Operation not permitted", quiet=True)
    assert [c.new_state for c in changes] == [capabilities.DEGRADED]
    assert capabilities.get(capabilities.SANDBOX).reason == "native sandbox failed"


def test_nothing_usable_is_unavailable(sandbox):
    engine, state = sandbox
    state["native_missing"] = ["sandbox-runtime"]
    state["docker"] = False
    engine.verify_sandbox_backend()
    entry = capabilities.get(capabilities.SANDBOX)
    assert entry.state == capabilities.UNAVAILABLE
    assert "refused" in entry.impact


def test_docker_by_choice_is_ok_but_still_notes_network(sandbox, monkeypatch):
    engine, _ = sandbox
    monkeypatch.setattr(engine, "SANDBOX_BACKEND", "docker")
    engine.verify_sandbox_backend()
    entry = capabilities.get(capabilities.SANDBOX)
    assert entry.state == capabilities.OK and entry.active == "docker"
    assert "no network" in engine._sandbox_model_note()


def test_command_result_carries_docker_note(sandbox, monkeypatch):
    engine, state = sandbox
    state["native_missing"] = ["sandbox-runtime"]
    engine.verify_sandbox_backend()
    monkeypatch.setattr(engine, "_run_sandbox_command",
                        lambda *a, **k: "ERROR: Could not find a version that satisfies")
    out = engine._exec_sandbox_command("pip install requests")
    assert out.startswith("ERROR: Could not find")
    assert "[note: this ran in the Docker sandbox" in out


def test_command_result_unchanged_on_native(sandbox, monkeypatch):
    engine, _ = sandbox
    engine.verify_sandbox_backend()
    monkeypatch.setattr(engine, "_run_sandbox_command", lambda *a, **k: "ok")
    assert engine._exec_sandbox_command("echo ok") == "ok"
