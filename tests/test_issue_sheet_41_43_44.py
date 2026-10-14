"""Issue-sheet fixes S41, S43, S44.

S41 the plan and claimed test files must exist on disk, not only in chat.
S43 a web turn leaves a trace file like a CLI turn does.
S44 a headless --prompt-file run saves a session and can be resumed.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent8088 import completion  # noqa: E402


# --------------------------------------------------------------------- S41
def _roots(tmp_path):
    return [tmp_path]


def test_a_plan_is_written_to_disk(tmp_path, monkeypatch):
    from agent8088 import engine
    monkeypatch.setattr(engine, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(engine, "PERMISSION_MODE", "plan-only")
    monkeypatch.setattr(engine, "_plan_on_approval", lambda text: "full-auto")
    monkeypatch.setattr(engine, "_human_wait", lambda: _null_ctx())
    monkeypatch.setattr(engine, "PLAN_AUDIT", False)

    result = engine._exec_present_plan({"plan": "1. build it\n2. ship it"})
    assert "approved" in result.lower()
    saved = tmp_path / "PLAN.md"
    assert saved.is_file(), "the approved plan must exist on disk"
    assert "build it" in saved.read_text(encoding="utf-8")
    assert str(saved) in result, "the answer must say where the plan went"


def test_a_rejected_plan_is_still_on_disk(tmp_path, monkeypatch):
    from agent8088 import engine
    monkeypatch.setattr(engine, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(engine, "PERMISSION_MODE", "plan-only")
    monkeypatch.setattr(engine, "_plan_on_approval", lambda text: None)
    monkeypatch.setattr(engine, "_human_wait", lambda: _null_ctx())

    engine._exec_present_plan({"plan": "a plan the user will revise"})
    assert (tmp_path / "PLAN.md").is_file()


class _null_ctx:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_a_test_file_the_reply_claims_but_never_wrote_is_reported(tmp_path):
    missing = completion.missing_outputs(
        "add tests", "I added test_detection.py and three more tests.",
        [], _roots(tmp_path))
    assert "test_detection.py" in missing


@pytest.mark.parametrize("reply", [
    "The suite passed and I verified the result.",
    "I refactored the database layer and cleaned up config.py.",
    "Tests are run with pytest run_tests.",
])
def test_ordinary_prose_is_not_a_missing_test(tmp_path, reply):
    assert completion.missing_outputs("do work", reply, [], _roots(tmp_path)) == []


def test_a_test_file_that_does_exist_is_not_reported(tmp_path):
    (tmp_path / "test_detection.py").write_text("def test_x():\n    pass\n",
                                                encoding="utf-8")
    missing = completion.missing_outputs(
        "add tests", "I added test_detection.py.", [], _roots(tmp_path))
    assert missing == []


def test_a_written_test_the_reply_names_is_not_double_counted(tmp_path):
    target = tmp_path / "test_detection.py"
    target.write_text("def test_x():\n    pass\n", encoding="utf-8")
    missing = completion.missing_outputs(
        "add tests", "I added test_detection.py.", [str(target)], _roots(tmp_path))
    assert missing == []


# --------------------------------------------------------------------- S43
def test_a_web_turn_writes_a_trace_file(tmp_path, monkeypatch):
    """A web run used to keep its trace in memory only."""
    from agent8088 import web_server

    class FakeSession:
        live_turn = None
        conversation_trace = []
        last_trace = None
        trace_path = ""
        name = "chat"
        messages = []
        trajectory_state = {}

    class FakeCli:
        _default_trace_path = staticmethod(lambda: tmp_path / "trace.json")
        _write_private_text = staticmethod(
            lambda path, text: Path(path).write_text(text, encoding="utf-8"))
        A = type("A", (), {"MODEL_NAME": "test-model", "turn_usage": staticmethod(lambda: {})})()

    web_server._record_turn_trace(
        FakeCli(), FakeSession(), "do it", [{"tool": "write_file"}], 1.0, False)
    path = tmp_path / "trace.json"
    assert path.is_file(), "a web turn must leave a trace file"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["trace"][0]["input"] == "do it"


def test_the_trace_path_is_stable_across_turns(tmp_path):
    """One accumulating file per chat, not a new one per turn."""
    from agent8088 import web_server

    class FakeSession:
        live_turn = None
        conversation_trace = []
        last_trace = None
        trace_path = ""
        name = "chat"
        messages = []
        trajectory_state = {}

    calls = []

    class FakeCli:
        _default_trace_path = staticmethod(
            lambda: (calls.append(1), tmp_path / "trace.json")[1])
        _write_private_text = staticmethod(
            lambda path, text: Path(path).write_text(text, encoding="utf-8"))
        A = type("A", (), {"MODEL_NAME": "m", "turn_usage": staticmethod(lambda: {})})()

    s = FakeSession()
    for query in ("one", "two", "three"):
        web_server._record_turn_trace(FakeCli(), s, query, [], 0.5, False)
    assert len(calls) == 1, "the default path must be chosen once per chat"
    turns = json.loads((tmp_path / "trace.json").read_text(encoding="utf-8"))["trace"]
    assert [t["input"] for t in turns] == ["one", "two", "three"]


# --------------------------------------------------------------------- S44
def test_prompt_file_turn_is_saved_and_resumable(tmp_path, monkeypatch):
    from agent8088 import cli
    task = tmp_path / "task.txt"
    task.write_text("do the thing", encoding="utf-8")

    calls = []

    def fake_do_chat(query):
        calls.append(query)
        # autosave must already be on so _ensure_autosave_session claims a name
        assert cli.S.autosave is True, "a headless run must leave a session to resume"

    monkeypatch.setattr(cli, "do_chat", fake_do_chat)
    monkeypatch.setattr(cli, "_start_trace_export", lambda: None)
    monkeypatch.setattr(cli, "_save_trace_on_sigterm", lambda: None)
    monkeypatch.setattr(cli, "_restore_sigterm", lambda prev: None)
    monkeypatch.setattr(cli, "_flush_memory_capture", lambda: None)
    monkeypatch.setattr(cli.A, "verify_sandbox_backend", lambda: None)
    monkeypatch.setattr(cli, "_session_path", lambda name: tmp_path / f"{name}.json")
    monkeypatch.setattr(cli, "SESSIONS_DIR", tmp_path)

    cli.S.autosave = False
    cli.S.name = None
    assert cli._run_prompt_file(str(task)) == 0
    assert calls == ["do the thing"]
    # Saving the session itself is do_chat's job (via _ensure_autosave_session),
    # which is stubbed here; the contract this path owns is that autosave is on
    # so that job has something to do.
    assert cli.S.autosave is True


def test_resuming_an_unknown_session_fails_loudly(tmp_path, monkeypatch):
    from agent8088 import cli
    task = tmp_path / "task.txt"
    task.write_text("continue", encoding="utf-8")
    monkeypatch.setattr(cli, "_session_path", lambda name: tmp_path / f"{name}.json")
    monkeypatch.setattr(cli, "SESSIONS_DIR", tmp_path)
    monkeypatch.setattr(cli, "S", cli.S)
    cli.S.name = None
    assert cli._run_prompt_file(str(task), resume="no-such-session") == 2
