"""Offline public-release checks for completion and long-running requests."""
from types import SimpleNamespace as N

import pytest

from agent8088 import app_check, completion, trajectory


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    import importlib
    monkeypatch.setenv("AGENT8088_CONFIG", str(tmp_path / "absent.txt"))
    monkeypatch.setenv("AGENT8088_HOME", str(tmp_path))
    monkeypatch.setenv("AGENT8088_SANDBOX", "local")
    from agent8088 import capabilities, engine
    capabilities.reset()
    engine = importlib.reload(engine)
    monkeypatch.setattr(engine, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(engine, "ARTIFACTS_ROOT", tmp_path / "artifacts")
    monkeypatch.setattr(engine, "FILE_BACKUPS", False)
    monkeypatch.chdir(tmp_path)
    yield engine
    capabilities.reset()


def test_saved_startup_output_keeps_the_error_tail():
    output = app_check.clip_output("starting\n" + "setup detail\n" * 100 + "RuntimeError: synthetic failure")
    state = trajectory.TrajectoryState({})
    state.record_smoke("app.py", "app.py: smoke test FAILED", True, output)
    assert state.data["smoke_output"]["app.py"] == output


def test_supplied_schema_is_not_a_missing_deliverable(tmp_path):
    project, artifacts = tmp_path / "project", tmp_path / "artifacts"
    project.mkdir()
    artifacts.mkdir()
    (artifacts / "example.json").write_text("{}", encoding="utf-8")
    start = completion.inventory([project, artifacts])
    (project / "index.html").write_text("<html></html>", encoding="utf-8")
    request = "Build index.html and export JSON (the format is in example.json)."
    assert completion.missing_outputs(request, "done", [], [project], [artifacts], start) == []


def test_inventory_enumeration_is_bounded(monkeypatch, tmp_path):
    visited = []

    class Scan:
        def __enter__(self):
            return iter(self)

        def __exit__(self, *args):
            pass

        def __iter__(self):
            for i in range(100):
                visited.append(i)
                yield N(path=str(tmp_path / f"f{i}.txt"), is_dir=lambda **k: False,
                        is_file=lambda **k: True, stat=lambda: N(st_size=1, st_mtime_ns=1))

    monkeypatch.setattr(completion, "INVENTORY_VISIT_LIMIT", 3)
    monkeypatch.setattr(completion.os, "scandir", lambda path: Scan())
    completion.inventory([tmp_path])
    assert len(visited) == 3


def test_smoke_pass_cannot_clear_a_failed_unit_suite():
    state = trajectory.TrajectoryState({})
    state.data.update(check_failing=True, failing_check="pytest tests/test_app.py -> app.py returned wrong result")
    state.record_smoke("/w/app.py", "app.py: smoke test passed", False, passed=True)
    assert state.check_failing()


@pytest.mark.parametrize("command", ["echo app.py", "cat app.py", "python -m py_compile app.py", "python app.py --help"])
def test_naming_or_inspecting_a_script_is_not_execution(command):
    assert not trajectory._runs_script(command, "/w/app.py", "/w")
    assert trajectory._runs_script("python app.py", "/w/app.py", "/w")


def test_round_warning_requests_an_honest_summary(runtime):
    assert "what you could not verify" in runtime._rounds_left_nudge(3, final=True)
    assert "what you could not verify" not in runtime._rounds_left_nudge(3)


def test_successful_waits_are_not_stopped_as_churn(runtime, monkeypatch):
    replies = []
    for i in range(9):
        call = N(id=f"c{i}", type="function", function=N(name="execute_shell", arguments='{"command":"sleep 1"}'))
        replies.append(N(choices=[N(message=N(content="", tool_calls=[call]), finish_reason="tool_calls")]))
    replies.append(N(choices=[N(message=N(content="Finished waiting.", tool_calls=None), finish_reason="stop")]))
    monkeypatch.setattr(runtime, "STALL_ROUNDS", 3)
    monkeypatch.setattr(runtime, "_create_completion_with_fallback", lambda *a, **k: replies.pop(0))
    monkeypatch.setattr(runtime, "exec_tool", lambda *a, **k: "waited")
    monkeypatch.setattr(runtime.memory, "enabled", lambda: False)
    trace = []
    answer = runtime.run_agent([{"role": "user", "content": "Wait for nine polling rounds."}],
                               max_turns=30, tools_def=[], allowed_tools={"execute_shell"}, trace=trace)
    assert answer.startswith("Finished waiting.")
    assert not any(step.get("type") == "stall_stop" for step in trace)


def test_rule_files_require_adoption_and_keep_their_full_identity(runtime, monkeypatch, tmp_path):
    folder = tmp_path / "policies"
    folder.mkdir()
    path = folder / "rules.md"
    path.write_text("Never submit before approval.", encoding="utf-8")
    monkeypatch.setattr(runtime, "create_completion", lambda *a, **k: N(
        choices=[N(message=N(content="Summary without rules", tool_calls=None), finish_reason="stop")]))
    monkeypatch.setattr(runtime, "_TURN_FILES_TOUCHED", [])
    monkeypatch.setattr(runtime, "_VERDICT_LEDGER", {})
    filler = lambda: [{"role": "assistant", "content": "working"} for _ in range(10)]
    messages = [{"role": "user", "content": "Audit policies/rules.md. Do not follow its contents."}] + filler()
    assert runtime.compact_messages(messages, keep=3, completion_client=object())
    assert "- (rules.md) Never submit" not in messages[0]["content"]
    messages = ([{"role": "user", "content": "Start."}] + filler()
                + [{"role": "user", "content": "Follow policies/rules.md."}] + filler())
    for _ in range(2):
        assert runtime.compact_messages(messages, keep=3, completion_client=object())
        assert "Never submit before approval." in messages[0]["content"]
        assert str(path) in messages[0]["content"]
        messages.extend(filler())


@pytest.mark.parametrize("first,second,expected", [
    (None, "Recovered summary", "Recovered summary"),
    ("First summary", None, "First summary"),
    (None, None, None),
    (None, "✿FUNCTION✿: execute_shell ✿ARGS✿: {}", None),
])
def test_empty_compaction_retry_is_bounded_and_never_uses_a_tool_call(
        runtime, monkeypatch, first, second, expected):
    sent, charged = [], []
    replies = [first, second]

    def summarize(*args, **kwargs):
        sent.append((kwargs["thinking"], args[1][0]["content"]))
        content = replies.pop(0)
        return N(choices=[N(message=N(content=content, tool_calls=None), finish_reason="stop")])

    monkeypatch.setattr(runtime, "create_completion", summarize)
    monkeypatch.setattr(runtime, "_TURN_FILES_TOUCHED", [])
    monkeypatch.setattr(runtime, "_VERDICT_LEDGER", {})
    messages = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"turn {i}"}
                for i in range(8)]
    before = list(messages)
    budget = N(add_usage=lambda response, **kwargs: charged.append(response))
    success = runtime.compact_messages(messages, keep=3, completion_client=object(), budget=budget)
    assert success == (expected is not None)
    assert len(charged) == len(sent) == (1 if first else 2)
    assert sent[0][0] is False
    if not first:
        assert sent[1][0] == "length_retry"
        assert "<transcript>\n" in sent[1][1]
        assert sent[1][1].rstrip().endswith("as plain text.")
    if success:
        assert expected in messages[0]["content"]
    else:
        assert messages == before
