"""Approval policy: denial circuit breaker, cron mode, and approval modes.

Mirrors Hermes' `approvals:` config block (website/docs/user-guide/security.md):

    approvals:
      mode: smart | manual | off
      timeout: 300
      cron_mode: deny | approve
      denial_breaker_threshold: 3
      mcp_reload_confirm: true
      destructive_slash_confirm: true

Agent8088 keys are flat: approval_mode, cron_mode, denial_breaker_threshold, etc.
Defaults keep existing behaviour: `manual` mode, breaker on at 3, cron denies.
"""
import pytest

from agent8088 import engine as A


# --- Denial circuit breaker ------------------------------------------------

def test_breaker_opens_on_the_threshold_denial(engine):
    engine.reset_approval_state()
    for _ in range(engine.DENIAL_BREAKER_THRESHOLD - 1):
        assert engine.note_denial() is False   # not tripped yet
    assert engine.note_denial() is True        # the Nth denial trips it
    assert engine.breaker_tripped() is True


def test_breaker_not_tripped_below_threshold(engine):
    engine.reset_approval_state()
    for _ in range(engine.DENIAL_BREAKER_THRESHOLD - 1):
        engine.note_denial()
    assert engine.breaker_tripped() is False


def test_approval_resets_the_breaker(engine):
    """A successful approval means the operator is engaged — start over."""
    engine.reset_approval_state()
    for _ in range(engine.DENIAL_BREAKER_THRESHOLD):
        engine.note_denial()
    assert engine.breaker_tripped() is True
    engine.note_approval()
    assert engine.breaker_tripped() is False


def test_threshold_zero_disables_the_breaker(engine, monkeypatch):
    monkeypatch.setattr(engine, "DENIAL_BREAKER_THRESHOLD", 0)
    engine.reset_approval_state()
    for _ in range(50):
        engine.note_denial()
    assert engine.breaker_tripped() is False


def test_breaker_message_tells_the_model_to_stop(engine):
    engine.reset_approval_state()
    for _ in range(engine.DENIAL_BREAKER_THRESHOLD):
        engine.note_denial()
    msg = engine.breaker_message()
    assert "denied" in msg.lower()
    assert "stop" in msg.lower()
    assert "denial_breaker_threshold" in msg


def test_breaker_resets_per_turn(engine, monkeypatch):
    """A new request starts with a clean slate, like the write counters."""
    monkeypatch.setattr(engine, "_create_completion_with_fallback",
                        lambda *a, **kw: type("R", (), {
                            "usage": None,
                            "choices": [type("C", (), {
                                "message": type("M", (), {"content": "done"})(),
                                "finish_reason": "stop",
                            })()],
                        })())
    for _ in range(engine.DENIAL_BREAKER_THRESHOLD):
        engine.note_denial()
    assert engine.breaker_tripped() is True
    engine.run_agent([{"role": "user", "content": "hi"}], max_turns=1)
    assert engine.breaker_tripped() is False


def test_run_agent_stops_the_loop_when_the_breaker_trips(engine, monkeypatch):
    """The model must be told to stop, not left retrying the same blocked call."""
    calls = {"n": 0}

    def _fake(messages, tools, **kw):
        calls["n"] += 1
        # A distinct shell command each turn: escalates in readonly, and a new
        # signature each time so run_agent's repeat cache does not short-circuit.
        content = ('✿FUNCTION✿: execute_shell ✿ARGS✿: '
                   '{"command": "rm -rf build%d"}' % calls["n"])
        return type("R", (), {
            "usage": None,
            "choices": [type("C", (), {
                "message": type("M", (), {"content": content})(),
                "finish_reason": "stop",
            })()],
        })()

    monkeypatch.setattr(engine, "_create_completion_with_fallback", _fake)
    monkeypatch.setattr(engine, "PERMISSION_MODE", "readonly")
    monkeypatch.setattr(engine, "DENIAL_BREAKER_THRESHOLD", 2)

    answer = engine.run_agent(
        [{"role": "user", "content": "write ten files"}],
        max_turns=10,
        on_escalation=lambda name, result: False,   # user denies every time
    )
    # Stops at the breaker rather than burning all 10 turns.
    assert calls["n"] <= 4
    assert "denied" in answer.lower()


# --- Cron approval policy --------------------------------------------------

def test_cron_mode_defaults_to_deny(engine):
    assert engine.CRON_MODE == "deny"


def test_unattended_run_denies_escalation_by_default(engine, monkeypatch):
    """A scheduled run has no operator, so a gate it hits must fail closed."""
    monkeypatch.setattr(engine, "UNATTENDED", True)
    monkeypatch.setattr(engine, "CRON_MODE", "deny")
    monkeypatch.setattr(engine, "PERMISSION_MODE", "readonly")
    result = engine.run_tool("execute_shell", {"command": "rm -rf build"})
    assert not result.startswith("ESCALATION_REQUEST:")
    assert "unattended" in result.lower()
    assert "cron_mode" in result


def test_unattended_run_can_be_configured_to_approve(engine, monkeypatch):
    monkeypatch.setattr(engine, "UNATTENDED", True)
    monkeypatch.setattr(engine, "CRON_MODE", "approve")
    monkeypatch.setattr(engine, "PERMISSION_MODE", "readonly")
    monkeypatch.setattr(engine, "_exec_shell_command", lambda *a, **kw: "OK")
    monkeypatch.setattr(engine, "_resolve_sandbox_backend", lambda: "native")
    result = engine.run_tool("execute_shell", {"command": "echo hi"})
    assert not result.startswith("ESCALATION_REQUEST:")


def test_unattended_approve_does_not_unlock_the_always_on_floor(engine, monkeypatch):
    """cron_mode=approve grants the escalatable gate, never the hard floor."""
    monkeypatch.setattr(engine, "UNATTENDED", True)
    monkeypatch.setattr(engine, "CRON_MODE", "approve")
    assert "forbidden" in engine.run_tool(
        "execute_shell", {"command": "rm -rf /"}).lower()


def test_attended_run_still_escalates_normally(engine, monkeypatch):
    monkeypatch.setattr(engine, "UNATTENDED", False)
    monkeypatch.setattr(engine, "PERMISSION_MODE", "readonly")
    result = engine.run_tool("execute_shell", {"command": "rm -rf build"})
    assert result.startswith("ESCALATION_REQUEST:")


def test_unattended_denial_is_audited(engine, monkeypatch, tmp_path):
    path = tmp_path / "audit.jsonl"
    monkeypatch.setattr(engine, "AUDIT_LOG_PATH", path)
    monkeypatch.setattr(engine, "AUDIT_ENABLED", True)
    monkeypatch.setattr(engine, "UNATTENDED", True)
    monkeypatch.setattr(engine, "CRON_MODE", "deny")
    monkeypatch.setattr(engine, "PERMISSION_MODE", "readonly")
    engine.run_tool("execute_shell", {"command": "rm -rf build"})
    import json
    entries = [json.loads(l) for l in path.read_text().splitlines()]
    assert any(e.get("reason") == "unattended_deny" for e in entries)


# --- Approval modes --------------------------------------------------------

def test_approval_mode_defaults_to_manual(engine):
    """smart adds a paid model call per gated command — opt in, don't inherit."""
    assert engine.APPROVAL_MODE == "manual"


def test_off_mode_requires_explicit_opt_in(engine):
    assert engine.APPROVAL_MODE != "off"


def test_invalid_approval_mode_falls_back_to_manual(engine, monkeypatch):
    monkeypatch.setitem(engine.APP_CONFIG, "approval_mode", "nonsense")
    assert engine._resolve_approval_mode() == "manual"


@pytest.mark.parametrize("mode", ["smart", "manual", "off"])
def test_valid_approval_modes_are_accepted(engine, monkeypatch, mode):
    monkeypatch.setitem(engine.APP_CONFIG, "approval_mode", mode)
    assert engine._resolve_approval_mode() == mode


# --- Unattended entry points must mark themselves ---------------------------

def test_cron_entry_sets_the_unattended_env_var(engine, monkeypatch):
    """A scheduled run must announce that no operator is present."""
    captured = {}

    def _fake_run(argv, **kw):
        if argv[:2] == ["crontab", "-"]:
            captured["payload"] = kw.get("input", "")
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(engine.subprocess, "run", _fake_run)
    engine._exec_cron({"action": "add", "schedule": "0 9 * * *", "task": "check mail"})
    assert "AGENT8088_UNATTENDED=1" in captured["payload"]


def test_windows_task_script_sets_the_unattended_env_var(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "_agent_data_dir", lambda: tmp_path)
    script = engine._windows_task_script("test-id", "check mail")
    assert "AGENT8088_UNATTENDED" in script.read_text()


def test_unattended_is_frozen_at_import(engine, monkeypatch):
    """Reading the env var per call would let anything in-process flip it mid-turn.

    Hermes freezes HERMES_YOLO_MODE at import for exactly this reason: an
    env-var check on the hot path is a prompt-injection escalation route.
    """
    monkeypatch.setenv("AGENT8088_UNATTENDED", "1")
    assert engine.UNATTENDED is False   # still the import-time value
