"""A blocked step must not read as a successful one.

Found by running the auditor against the real model: a shell step that was never
approved and never executed was recorded as a success, and the plan carried on
past it. Shell results are wrapped in untrusted-content markers before the plan
executor sees them, so the ESCALATION_REQUEST prefix every caller matches on
was no longer at the start of the string.

Three call sites shared that mistake — the failure check, the escalation retry in
_exec_plan, and _handle_escalation in the CLI — so the fix is upstream: an
escalation request is a control signal, not command output, and is not wrapped.
"""
import json


WRAPPED_ESCALATION = (
    '<<<EXTERNAL_UNTRUSTED_CONTENT source="shell command: echo hi">>>\n'
    "ESCALATION_REQUEST\x1fedit\x1flocal_execution\x1fC:\\repo\x1fRun this locally?\n"
    "<<<END_UNTRUSTED_CONTENT>>>"
)


def test_wrapped_escalation_counts_as_a_failed_step(engine):
    assert engine._plan_step_failed(WRAPPED_ESCALATION) is True


def test_wrapped_error_counts_as_a_failed_step(engine):
    wrapped = ('<<<EXTERNAL_UNTRUSTED_CONTENT source="x">>>\n'
               "Error: boom\n<<<END_UNTRUSTED_CONTENT>>>")
    assert engine._plan_step_failed(wrapped) is True


def test_command_output_merely_mentioning_error_still_passes(engine):
    """Unwrap and check the prefix; do not search the whole body.

    A build log that prints 'Error: 0 warnings' is a passing step, and halting on
    it would make the guard worse than not having one.
    """
    wrapped = ('<<<EXTERNAL_UNTRUSTED_CONTENT source="build">>>\n'
               "Build succeeded.\nError: 0 warnings, 0 errors\n"
               "<<<END_UNTRUSTED_CONTENT>>>")
    assert engine._plan_step_failed(wrapped) is False


def test_unwrap_leaves_unwrapped_text_alone(engine):
    assert engine._unwrap_untrusted("Error: plain") == "Error: plain"
    assert engine._unwrap_untrusted("") == ""


def test_shell_escalation_is_not_wrapped(engine, monkeypatch):
    """The root fix: the request stays parseable by every caller."""
    monkeypatch.setattr(engine, "PERMISSION_MODE", "full-auto")
    monkeypatch.setattr(
        engine, "_exec_shell_command",
        lambda *a, **k: "ESCALATION_REQUEST\x1fedit\x1flocal_execution\x1fC:\\repo\x1fRun locally?")
    result = engine.run_tool("execute_shell", {"command": "echo hi"})
    assert result.startswith("ESCALATION_REQUEST\x1f"), (
        "a control signal must not be wrapped as untrusted output")
    assert "EXTERNAL_UNTRUSTED_CONTENT" not in result


def test_normal_shell_output_is_still_wrapped(engine, monkeypatch):
    monkeypatch.setattr(engine, "PERMISSION_MODE", "full-auto")
    monkeypatch.setattr(engine, "_exec_shell_command", lambda *a, **k: "hello")
    result = engine.run_tool("execute_shell", {"command": "echo hi"})
    assert "EXTERNAL_UNTRUSTED_CONTENT" in result, "real output must stay wrapped"


def test_blocked_shell_step_halts_the_plan(engine, tmp_path, monkeypatch):
    """End to end: the step never ran, so nothing after it may run either."""
    engine.ALLOWED_PATHS = [tmp_path]
    engine.PERMISSION_MODE = "full-auto"
    monkeypatch.setattr(
        engine, "_exec_shell_command",
        lambda *a, **k: "ESCALATION_REQUEST\x1fedit\x1flocal_execution\x1fC:\\repo\x1fRun locally?")
    later = tmp_path / "later.txt"
    out = engine._exec_plan({"steps": json.dumps([
        {"tool": "execute_shell", "arguments": {"command": "echo hi"}},
        {"tool": "write_file", "arguments": {"filename": str(later), "content": "x"}},
    ])})
    assert not later.exists(), "a step after an unapproved step must not run"
    assert "halted at step 1/2" in out


def test_plan_offers_approval_for_a_blocked_shell_step(engine, tmp_path, monkeypatch):
    """The retry branch matched the wrapped string, so no prompt was ever shown."""
    engine.ALLOWED_PATHS = [tmp_path]
    engine.PERMISSION_MODE = "full-auto"
    calls = {"n": 0}

    def _shell(*_a, **_k):
        calls["n"] += 1
        if calls["n"] == 1:
            return "ESCALATION_REQUEST\x1fedit\x1flocal_execution\x1fC:\\repo\x1fRun locally?"
        return "hello"

    monkeypatch.setattr(engine, "_exec_shell_command", _shell)
    seen = []
    out = engine._exec_plan(
        {"steps": json.dumps([{"tool": "execute_shell", "arguments": {"command": "echo hi"}}])},
        on_escalation=lambda request: seen.append(request) or True,
    )
    assert seen, "the user must be offered the approval"
    assert seen[0].startswith("ESCALATION_REQUEST\x1f")
    assert "halted" not in out, "approving should let the step through"
