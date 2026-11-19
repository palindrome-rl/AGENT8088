"""Audit issues 1-7: what counts as proof that changed work was checked."""
import pytest

from agent8088 import completion, tool_output as T
from agent8088.trajectory import TrajectoryState as S


def _after_write(path="app.py", existed=False):
    s = S("fix it", "")
    op = s.before_tool("write_file")
    s.after_tool(op, "Wrote", failed=False, blocked=False, mutated=True, path=path, existed=existed)
    return s


def _run(s, tool, result, status, **kw):
    op = s.before_tool(tool)
    s.after_tool(op, result, failed=False, blocked=False, mutated=False, verdict=status, **kw)


# 1 -- text printed by a command is not a test result
@pytest.mark.parametrize("name,args,expected", [
    ("run_tests", {}, "passed"),
    ("execute_shell", {"command": "pytest -q"}, "passed"),
    ("execute_shell", {"command": "cd web && npm test"}, "passed"),
    ("execute_shell", {"command": 'echo "All 3 tests passed"'}, "unknown"),
    ("execute_shell", {"command": 'pytest; echo "3 passed"'}, "unknown"),
    ("read_text", {"filename": "notes.txt"}, "unknown"),
])
def test_only_real_runners_can_pass(engine, name, args, expected):
    assert engine._evidence_status(name, args, "3 tests passed", "passed", "summary line") == expected


def test_a_failing_exit_status_still_counts_without_a_runner(engine):
    assert engine._evidence_status("execute_shell", {"command": "python app.py"},
                                   "Command exited with status 1", "failed", "exit status") == "failed"
    assert engine._evidence_status("read_text", {}, "5 failed", "failed", "summary line") == "unknown"


def test_echoed_pass_does_not_verify_the_change(engine):
    s = _after_write()
    status = engine._evidence_status("execute_shell", {"command": 'echo "All 3 tests passed"'},
                                     "All 3 tests passed", "passed", "summary line")
    _run(s, "execute_shell", "All 3 tests passed", status, command='echo "All 3 tests passed"')
    assert s.data["verification"] != "verified"


# 2 -- an unrelated read is not a check
def test_unrelated_ls_keeps_the_reminder_on():
    s = _after_write("app.py")
    _run(s, "execute_shell", "a b c", "unknown", command="ls")
    assert s.needs_verification()
    _run(s, "read_text", "other", "unknown", path="README.md")
    assert s.needs_verification()


def test_running_or_reading_the_changed_file_counts():
    s = _after_write("src/app.py")
    _run(s, "execute_shell", "ok", "unknown", command="python src/app.py")
    assert not s.needs_verification()
    s = _after_write("src/app.py")
    _run(s, "read_text", "code", "unknown", path="src/app.py")
    assert not s.needs_verification()
    s = _after_write("src/app.py")
    _run(s, "git_diff", "diff", "unknown")
    assert not s.needs_verification()


# 5 -- JS and Go formats, and prose that mentions "error"
@pytest.mark.parametrize("text,expected", [
    ("  12 passing (3s)", "passed"),
    ("  5 passing (1s)\n  1 failing", "failed"),
    ("ok  \tgithub.com/x/pkg\t0.021s", "passed"),
    ("ok  \tgithub.com/x/pkg\t(cached)", "passed"),
    ("FAIL\tgithub.com/x/pkg\t0.02s", "failed"),
    ("handles 1 error case", "unknown"),
    ("= 1 error in 0.30s =", "failed"),
    ("1 passed, 1 error in 0.2s", "failed"),
    ("Tests:       2 failed, 5 passed, 7 total", "failed"),
])
def test_runner_output_formats(text, expected):
    assert T.detect_verdict(text).status == expected


# 6 -- the reminder names the files and the command
def test_reminder_names_file_and_command():
    assert "`python app.py`" in completion.verify_ask(["/w/app.py"], False)
    assert "`run_tests`" in completion.verify_ask(["/w/app.py"], True)
    assert "`node a.js`" in completion.verify_ask(["a.js", "b.js"], False)
    assert "check_app" in completion.verify_ask(["index.html"], False)
    assert completion.verify_ask([], False) == completion.VERIFY_ASK


# 7 -- edits to a test that already existed are flagged
def test_existing_test_edit_is_flagged_but_new_tests_are_not(engine):
    s = _after_write("tests/test_a.py", existed=True)
    assert s.edited_existing_tests() == ["tests/test_a.py"]
    assert "Test-integrity note" in engine._with_task_notes("done", s)
    s = _after_write("tests/test_new.py", existed=False)
    op = s.before_tool("edit_file")
    s.after_tool(op, "Updated", failed=False, blocked=False, mutated=True,
                 path="tests/test_new.py", existed=True)
    assert s.edited_existing_tests() == []
