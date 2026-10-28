"""The end-of-run notes name the check that is failing and say why.

Seen in overnight runs: "NOT VERIFIED: the latest check failed. Details are in the notes at the end." was followed by a note that
only said the check "was not fixed". Nothing said which check, so a reader could not tell a real failure from a broken check: a
Unix `tail` on Windows, a probe of the local model server, a path typo in the model's own validation script."""
import pytest

from agent8088 import trajectory
from agent8088.trajectory import TrajectoryState, _error_line


def _record(state, tool, verdict, command="", result=""):
    op = state.before_tool(tool)
    state.after_tool(op, result, failed=False, blocked=False, mutated=False, verdict=verdict, command=command)


def _state():
    return TrajectoryState({}, goal="x", message_count=1)


WRAPPED = ('<<<EXTERNAL_UNTRUSTED_CONTENT source="shell command: cd /d E:\\run\\work && node tools\\validate.mjs">>>\n'
           "node:internal/fs/utils:351\n"
           "    throw err;\n"
           "Error: ENOENT: no such file or directory, open 'E:\\E:\\run\\work\\data\\pokemon.json'\n"
           "    at Object.openSync (node:fs:573:18)\n"
           "Node.js v22.11.0\n"
           "Command exited with status 1.\n"
           "<<<END_UNTRUSTED_CONTENT>>>")


def test_the_note_names_the_command_and_the_error_line():
    state = _state()
    _record(state, "execute_shell", "failed", command="cd /d E:\\run\\work && node tools\\validate.mjs", result=WRAPPED)
    note = state.failing_check_note()
    assert note.startswith("Failing check: `node tools\\validate.mjs`")
    assert "ENOENT" in note and "pokemon.json" in note
    assert "UNTRUSTED" not in note and "exited with status" not in note
    assert "\n" not in note


def test_the_error_line_is_the_last_one_that_reads_like_an_error():
    assert _error_line("running tests\n3 passed\nAssertionError: expected 4, got 5\nsee report.html") == \
        "AssertionError: expected 4, got 5"
    assert _error_line("one\ntwo\nthree") == "three"                                   # nothing error-like: the last line
    assert _error_line("<<<END_UNTRUSTED_CONTENT>>>\nCommand exited with status 1.") == ""


def test_a_unix_tool_missing_on_windows_is_named_as_such():
    state = _state()
    _record(state, "execute_shell", "failed", command="node --check app.js && echo ok | tail -20",
            result="'tail' is not recognized as an internal or external command,\noperable program or batch file.\nCommand exited with status 255.")
    note = state.failing_check_note()
    assert "`node --check app.js && echo ok | tail -20`" in note and "'tail' is not recognized" in note


def test_a_failed_page_check_reads_as_check_app_with_its_message():
    state = _state()
    _record(state, "check_app", "failed", result="index.html: smoke test FAILED - clicking the first control changed nothing")
    note = state.failing_check_note()
    assert note.startswith("Failing check: check_app (index.html: smoke test FAILED")
    assert "`" not in note


def test_a_long_command_is_shortened():
    state = _state()
    _record(state, "execute_shell", "failed", command="pytest -q " + "tests/test_very_long_name_here.py " * 6, result="Error: boom")
    note = state.failing_check_note()
    assert "..." in note and len(note) < 200


def test_nothing_is_said_when_no_check_is_failing():
    state = _state()
    assert state.failing_check_note() == ""
    _record(state, "execute_shell", "failed", command="pytest -q", result="1 failed")
    _record(state, "execute_shell", "passed", command="pytest -q", result="3 passed")
    assert state.failing_check_note() == ""


def test_state_without_the_new_fields_still_gets_a_note():
    state = _state()
    state.data.update(check_failing=True, failing_check="python test_app.py -> Traceback\nAssertionError: bad total")
    note = state.failing_check_note()
    assert note.startswith("Failing check: `python test_app.py`") and "bad total" in note


def test_the_final_answer_carries_the_note_after_the_existing_sentence(engine):
    state = _state()
    _record(state, "execute_shell", "failed", command="node validate.mjs", result="Error: ENOENT: no such file")
    answer = engine._with_task_notes("Done.", state)
    assert answer.startswith("NOT VERIFIED: the latest check failed. Details are in the notes at the end.")
    assert f"{engine.CHECKS_FAILED_NOTE} Failing check: `node validate.mjs` (Error: ENOENT: no such file)." in answer


def test_the_existing_sentence_alone_is_kept_when_nothing_is_known(engine):
    state = _state()
    state.data.update(check_failing=True, failing_check="")
    answer = engine._with_task_notes("Done.", state)
    assert engine.CHECKS_FAILED_NOTE in answer and "Failing check" not in answer
