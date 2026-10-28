"""After a few code or page files change with nothing run or opened, the next tool result says to run what was written.

Verification studies of agent harnesses find that agents run or inspect the result after only a small share of their edits
(6-13% in one study), and that a short reminder sent right after an edit raises accuracy without more output."""
import json

import pytest

from agent8088.trajectory import EditCheckReminder


def _write(rem, path, **kw):
    return rem.after_tool("write_file", {"filename": path, "content": "x"}, "Wrote 1 bytes", mutated=True, path=path, **kw)


def test_the_third_changed_file_gets_a_reminder():
    rem = EditCheckReminder(after=3, limit=3)
    assert _write(rem, "app/a.js") == ""
    assert _write(rem, "app/b.js") == ""
    text = _write(rem, "app/index.html")
    assert "a.js" in text and "b.js" in text and "index.html" in text and "real entry point" in text


def test_the_same_file_written_again_counts_once():
    rem = EditCheckReminder(after=3, limit=3)
    for _ in range(5):
        assert _write(rem, "app/a.js") == ""


def test_running_or_checking_clears_the_count():
    rem = EditCheckReminder(after=3, limit=3)
    _write(rem, "a.js")
    _write(rem, "b.js")
    assert rem.after_tool("execute_shell", {"command": "node a.js"}, "ok", mutated=False) == ""
    assert _write(rem, "c.js") == ""
    assert _write(rem, "d.js") == ""
    _write(rem, "e.js")  # third since the run: reminded
    rem2 = EditCheckReminder(after=2, limit=3)
    _write(rem2, "a.js")
    assert rem2.after_tool("check_app", {"path": "index.html"}, "index.html: smoke test passed", mutated=False) == ""
    assert _write(rem2, "b.js") == ""


def test_opening_a_local_page_clears_the_count():
    rem = EditCheckReminder(after=2, limit=3)
    _write(rem, "a.js")
    assert rem.after_tool("browse_page", {"url": "http://127.0.0.1:8000/"}, "page text", mutated=False) == ""
    assert _write(rem, "b.js") == ""


def test_a_failed_run_does_not_count_as_running():
    rem = EditCheckReminder(after=2, limit=3)
    _write(rem, "a.js")
    rem.after_tool("execute_shell", {"command": "node a.js"}, "Error: command failed", mutated=False)
    assert _write(rem, "b.js") != ""


@pytest.mark.parametrize("path", ["notes.md", "data.json", "tests/test_a.py", "test_a.py", "a.txt"])
def test_files_that_are_not_code_or_pages_never_count(path):
    rem = EditCheckReminder(after=1, limit=3)
    assert _write(rem, path) == ""


def test_a_file_written_in_parts_waits_for_its_last_part():
    rem = EditCheckReminder(after=2, limit=3)
    assert _write(rem, "a.js", partial=True) == ""
    assert _write(rem, "b.js", partial=True) == ""
    assert _write(rem, "c.js") != ""


def test_at_most_limit_reminders_and_zero_turns_it_off():
    rem = EditCheckReminder(after=1, limit=2)
    sent = [bool(_write(rem, f"f{i}.js")) for i in range(5)]
    assert sent == [True, True, False, False, False]
    off = EditCheckReminder(after=1, limit=0)
    assert _write(off, "a.js") == ""


def test_the_wording_names_check_app_only_when_it_exists():
    with_tool, without = EditCheckReminder(after=1, limit=1), EditCheckReminder(after=1, limit=1)
    assert "check_app" in _write(with_tool, "a.js", can_check_app=True)
    assert "check_app" not in _write(without, "a.js")


def _response(name, args):
    call = type("T", (), {"function": type("F", (), {"name": name, "arguments": json.dumps(args)})()})()
    message = type("M", (), {"content": "", "tool_calls": [call]})()
    return type("R", (), {"choices": [type("C", (), {"message": message, "finish_reason": "tool_calls"})()]})()


@pytest.mark.parametrize('can_check_app', [False, True])
def test_the_loop_adds_the_reminder_to_the_third_write_result(engine, monkeypatch, can_check_app):
    seen = []
    files = iter(["a.js", "b.js", "index.html"])

    def model(messages, tools, **kwargs):
        seen.append(json.dumps(messages[-1])[:4000])
        try:
            return _response("write_file", {"filename": next(files), "content": "x"})
        except StopIteration:
            raise RuntimeError("stop")

    def run_tool(name, args, **kw):
        engine._MUTATION_SEQ += 1
        return "Wrote 1 bytes"

    monkeypatch.setattr(engine, "_create_completion_with_fallback", model)
    monkeypatch.setattr(engine, "run_tool", run_tool)
    monkeypatch.setattr(engine, "PERMISSION_MODE", "full-auto")
    monkeypatch.setattr(engine, "EDIT_CHECK_REMINDERS", 3)
    trace = []
    try:
        engine.run_agent([{"role": "user", "content": "build the page"}], max_turns=10, trace=trace,
                         dynamic_turns=False, allowed_tools={'write_file'} | ({'check_app'} if can_check_app else set()))
    except RuntimeError:
        pass
    assert any(t["type"] == "edit_check_reminder" for t in trace)
    assert "since you last ran or opened anything" in seen[-1]
    assert all("since you last ran or opened anything" not in s for s in seen[:-1])
    assert ('use check_app' in seen[-1]) == can_check_app


@pytest.mark.parametrize('name,args,result', [
    ('execute_shell', {'command': 'pytest'}, '1 failed, 2 passed\nCommand exited with status 1.'),
    ('execute_shell', {'command': 'node a.js'}, 'Traceback (most recent call last):\nNameError: x is not defined'),
    ('check_app', {'path': 'index.html'}, 'index.html: smoke test FAILED - dead button'),
    ('check_app', {'path': 'index.html'}, 'index.html: smoke test unavailable'),
    ('browse_page', {'url': 'http://localhost.example.com/'}, 'opened page'),
    ('browse_page', {'url': 'http://127.0.0.1.example.com/'}, 'opened page'),
    ('browse_page', {'url': 'http://[broken]/'}, 'opened page'),
    ('get_page_title', {'url': 'http://localhost:8000/'}, 'Title only'),
    ('execute_shell', {'command': 'pytest --help'}, 'usage: pytest ...'),
    ('execute_shell', {'command': 'echo node a.js'}, 'node a.js'),
])
def test_failed_checks_unavailable_pages_and_non_executing_queries_preserve_count(name, args, result):
    rem = EditCheckReminder(after=2)
    _write(rem, 'a.js')
    rem.after_tool(name, args, result, mutated=False)
    assert _write(rem, 'b.js')


def test_explicit_failure_metadata_prevents_clearing():
    rem = EditCheckReminder(after=2)
    _write(rem, 'a.js')
    rem.after_tool('execute_shell', {'command': 'pytest'}, 'custom tool failure', mutated=False, failed=True)
    assert _write(rem, 'b.js')


def test_workspace_parent_names_do_not_turn_deliverables_into_tests_or_scratch(tmp_path):
    workspace = tmp_path / 'tests' / 'temp' / 'project'
    rem = EditCheckReminder(after=2, workspace=str(workspace))
    assert not _write(rem, str(workspace / 'a.js'))
    assert _write(rem, str(workspace / 'b.js'))


def test_relative_absolute_and_normalized_names_count_once(tmp_path):
    rem = EditCheckReminder(after=2, workspace=str(tmp_path))
    assert not _write(rem, 'app/a.js')
    assert not _write(rem, str(tmp_path / 'app' / 'a.js'))
    assert not _write(rem, 'app/./a.js')
    assert _write(rem, 'app/b.js')


def test_running_a_redirected_file_uses_the_actual_shell_workspace(tmp_path):
    rem = EditCheckReminder(after=2, workspace=str(tmp_path))
    artifacts = tmp_path / 'artifacts'
    _write(rem, str(artifacts / 'a.js'))
    rem.after_tool('execute_shell', {'command': 'node a.js'}, 'ok', mutated=False,
                   run_workspace=str(artifacts))
    assert not _write(rem, str(artifacts / 'b.js'))
