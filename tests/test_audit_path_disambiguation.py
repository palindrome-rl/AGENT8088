"""The auditor is told exactly which file to inspect, and which tool sees it.

A step wrote `library.py`. The auditor ran `wc -c library.py` and reported the
file was 4092 bytes with different contents, so the write "failed" and was
reverted — destroying correct work.

Both observations were true of different files. `write_file` and `read_text`
resolve a bare name against the project; `execute_shell` runs inside a
*disposable copy* of the sandbox workspace, where the same name is another file
entirely. The auditor compared one against a claim about the other.
"""
def _task_for(engine, monkeypatch, tool, args, **kwargs):
    """Capture the task text the auditor would receive."""
    seen = {}
    monkeypatch.setattr(engine, "_exec_subagent",
                        lambda a, depth=0: seen.update(task=a["task"]) or "VERDICT: pass — ok")
    engine._active_budget = engine._TurnBudget()
    engine._audit_plan_step("do the thing", tool, args, "Wrote 10 bytes", 0, **kwargs)
    return seen["task"]


def test_the_audit_names_the_absolute_path_that_was_written(engine, tmp_path, monkeypatch):
    engine.ALLOWED_PATHS = [tmp_path]
    monkeypatch.setattr(engine, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(engine, "ARTIFACTS_ROOT", tmp_path / "artifacts")
    target = tmp_path / "library.py"
    target.write_text("x", encoding="utf-8")

    task = _task_for(engine, monkeypatch, "write_file",
                     {"filename": str(target), "content": "x"})

    assert "touched exactly this path" in task
    assert str(target) in task


def test_the_audit_explains_that_shell_sees_a_disposable_copy(engine, tmp_path, monkeypatch):
    engine.ALLOWED_PATHS = [tmp_path]
    task = _task_for(engine, monkeypatch, "write_file",
                     {"filename": str(tmp_path / "a.txt"), "content": "x"})

    assert "DISPOSABLE COPY" in task
    assert "read_text reads the real file" in task
    assert "not evidence" in task


def test_the_audit_warns_against_failing_on_an_unconfirmed_path(engine, tmp_path, monkeypatch):
    """The specific mistake: a confident fail from the wrong file."""
    engine.ALLOWED_PATHS = [tmp_path]
    task = _task_for(engine, monkeypatch, "write_file",
                     {"filename": str(tmp_path / "a.txt"), "content": "x"})

    assert "never answer 'fail' from a path you have not confirmed" in task


def test_a_step_with_no_path_still_produces_a_task(engine, tmp_path, monkeypatch):
    """execute_shell has no path_arg; the audit must not break on that."""
    engine.ALLOWED_PATHS = [tmp_path]
    task = _task_for(engine, monkeypatch, "execute_shell", {"command": "echo hi"})

    assert "touched exactly this path" not in task
    assert "VERDICT" in task


def test_an_unresolvable_path_is_omitted_rather_than_raising(engine, tmp_path, monkeypatch):
    engine.ALLOWED_PATHS = [tmp_path]
    task = _task_for(engine, monkeypatch, "write_file",
                     {"filename": "\0invalid", "content": "x"})

    assert "VERDICT" in task, "a bad path must not abort the audit"


def test_the_auditor_profile_states_the_two_filesystems(engine):
    profile = engine.SUBAGENT_SPECS["auditor"]["system_prompt"]
    assert "disposable copy" in profile
    assert "read_text" in profile


# ---------------------------------------------------------------------------
# Native sandbox repair hint
# ---------------------------------------------------------------------------

def test_a_logon_failure_leads_with_the_runtime_error(engine):
    """The runtime's own error is the only text that identifies the failure.

    The wording this replaces asserted a cause instead: reprovision from an
    elevated terminal, or antivirus is blocking you. On a machine where the
    account was provisioned, seclogon was running, the terminal was elevated,
    the antivirus had been uninstalled and the runtime had been upgraded, it went
    on asserting all of them — while the one string that pinned the failure never
    reached the reader. A confident wrong answer is worse than the raw text it
    displaced, so the reason leads and the guesses come after it.
    """
    raw = ('srt-win: error: spawn runner for egress probe: '
           'CreateProcessWithLogonW(srt-sandbox): Access is denied. (0x80070005)')
    hint = engine._native_sandbox_repair_hint(raw)
    assert hint.startswith("Reason:")
    assert "CreateProcessWithLogonW(srt-sandbox)" in hint


def test_a_logon_failure_offers_checks_rather_than_a_diagnosis(engine):
    """Name what is worth checking, and name when it is not the reader's machine."""
    hint = engine._native_sandbox_repair_hint(
        'CreateProcessWithLogonW(srt-sandbox): Access is denied. (0x80070005)')
    lowered = hint.lower()
    assert "seclogon" in lowered
    assert "antivirus" in lowered
    assert "policy" in lowered
    # Provisioning succeeding while the spawn keeps failing is an upstream bug.
    # Saying so is what stops the reader re-running setup forever.
    assert "sandbox-runtime" in hint


def test_the_model_facing_error_keeps_runtime_text_out(engine):
    """`_sandbox_required_error` reaches the model as a tool result.

    Raw runtime stderr there reads as command output — the failure that had the
    agent report it could not verify its work and print an invented expected-
    output table. Human-facing callers want the reason; this one must not carry
    it, so the reason is opt-out rather than unconditional.
    """
    hint = engine._native_sandbox_repair_hint(
        'srt-win: error: CreateProcessWithLogonW(srt-sandbox): Access is denied.',
        include_reason=False)
    assert "srt-win" not in hint
    assert "seclogon" in hint.lower()


def test_a_latched_failure_logs_the_reason_and_not_the_advice(engine, caplog):
    """One `--sandbox-setup` run printed the same paragraph twice.

    `_mark_native_sandbox_broken` logged the full hint and then
    `install_native_sandbox` returned it again. The warning carries the reason,
    the command's return value carries the guidance, and neither repeats.
    """
    raw = 'CreateProcessWithLogonW(srt-sandbox): Access is denied. (0x80070005)'
    with caplog.at_level("WARNING"):
        engine._mark_native_sandbox_broken(raw)
    logged = caplog.text
    assert raw in logged
    assert "antivirus" not in logged.lower()


def test_a_missing_runtime_names_setup_too(engine):
    assert "--sandbox-setup" in engine._native_sandbox_repair_hint(
        "Native sandbox runtime is unavailable.")


def test_an_unrecognised_failure_still_reports_the_reason(engine):
    """Never swallow a cause we do not have a hint for."""
    hint = engine._native_sandbox_repair_hint("something entirely new went wrong")
    assert "something entirely new went wrong" in hint


def test_the_probe_stays_quiet_for_a_caller_that_reports_it_itself(
        engine, caplog, tmp_path, monkeypatch):
    """`--sandbox-setup` returns the failure, so the latch must not also log it."""
    monkeypatch.setattr(engine, "_native_sandbox_argv", lambda: None)
    with caplog.at_level("WARNING"):
        assert engine._native_sandbox_ready(tmp_path, quiet=True) is False
    assert caplog.text == ""


def test_the_probe_still_warns_for_every_other_caller(
        engine, caplog, tmp_path, monkeypatch):
    """Startup has no return value to carry it, so that path must keep warning."""
    monkeypatch.setattr(engine, "_native_sandbox_argv", lambda: None)
    with caplog.at_level("WARNING"):
        assert engine._native_sandbox_ready(tmp_path) is False
    assert "native sandbox could not start" in caplog.text
