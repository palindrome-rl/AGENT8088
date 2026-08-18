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

def test_a_logon_failure_names_the_command_that_fixes_it(engine):
    hint = engine._native_sandbox_repair_hint(
        'srt-win: error: spawn runner for egress probe: '
        'CreateProcessWithLogonW(srt-sandbox): Access is denied. (0x80070005)')
    assert "--sandbox-setup" in hint
    assert "elevated" in hint


def test_a_logon_failure_names_what_blocks_it_from_outside(engine):
    """Elevation is only half the answer, and the wrong half when already elevated.

    Re-running `--sandbox-setup` from an admin terminal is the first thing to try,
    but when that was already done the account exists and the logon is being
    refused by something else. Antivirus behaviour shields are the usual cause —
    CreateProcessWithLogonW against a freshly created local account is a textbook
    lateral-movement signature — and naming them is what stops the reader looping
    on "run it elevated" they have already done.
    """
    hint = engine._native_sandbox_repair_hint(
        'srt-win: error: spawn runner for egress probe: '
        'CreateProcessWithLogonW(srt-sandbox): Access is denied. (0x80070005)')
    assert "antivirus" in hint.lower()
    assert "seclogon" in hint.lower()


def test_a_missing_runtime_names_setup_too(engine):
    assert "--sandbox-setup" in engine._native_sandbox_repair_hint(
        "Native sandbox runtime is unavailable.")


def test_an_unrecognised_failure_still_reports_the_reason(engine):
    """Never swallow a cause we do not have a hint for."""
    hint = engine._native_sandbox_repair_hint("something entirely new went wrong")
    assert "something entirely new went wrong" in hint
