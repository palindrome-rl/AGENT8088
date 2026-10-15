import json

from agent8088.gateway.session import SessionStore
from agent8088.trajectory import TrajectoryState


def _response(content):
    message = type("Message", (), {"content": content, "tool_calls": None})()
    choice = type("Choice", (), {"message": message, "finish_reason": "stop"})()
    return type("Response", (), {"choices": [choice]})()


def test_mutation_needs_evidence_and_a_restart_never_retries_blindly():
    state = {}
    trajectory = TrajectoryState(state, "update the project", 1)
    operation = trajectory.before_tool("write_file")

    restarted = TrajectoryState(state, "update the project", 2)
    assert restarted.data["operations"][-1]["status"] == "unknown"

    operation = restarted.before_tool("execute_shell")
    restarted.after_tool(operation, "wrote file", failed=False, blocked=False,
                           mutated=True)
    assert restarted.needs_verification()
    assert restarted.request_verification() is True
    assert restarted.request_verification() is False

    verification = restarted.before_tool("execute_shell")
    restarted.after_tool(verification, "2 passed", failed=False, blocked=False,
                           mutated=False, verdict="passed")
    assert restarted.data["verification"] == "verified"


def test_gateway_session_keeps_optional_trajectory_and_reads_legacy_files(tmp_path):
    store = SessionStore(tmp_path)
    key = "agent:main:test"
    messages = [{"role": "user", "content": "hello"}]
    state = {"version": 1, "goal": "hello", "operations": []}

    store.save(key, messages, state)

    assert store.load(key) == messages
    assert store.load_trajectory(key) == state

    legacy = "agent:main:legacy"
    store._path(legacy).write_text(json.dumps(messages), encoding="utf-8")
    assert store.load(legacy) == messages
    assert store.load_trajectory(legacy) == {}


def test_main_loop_checkpoints_tool_intent_before_and_after_result(monkeypatch, engine):
    replies = iter([
        '✿FUNCTION✿: calculate ✿ARGS✿: {"expression":"1+1"}',
        "The answer is 2.",
    ])
    checkpoints = []
    state = {}

    monkeypatch.setattr(engine, "_create_completion_with_fallback",
                        lambda *args, **kwargs: _response(next(replies)))

    answer = engine.run_agent(
        [{"role": "user", "content": "calculate 1+1"}], max_turns=2,
        tools_def=[], allowed_tools={"calculate"}, trajectory_state=state,
        on_trajectory_state=lambda value: checkpoints.append(value),
    )

    assert answer == "The answer is 2."
    statuses = [snapshot["operations"][-1]["status"] for snapshot in checkpoints
                if snapshot["operations"]]
    assert statuses[:2] == ["running", "done"]
    assert state["operations"][-1]["tool"] == "calculate"


def test_main_loop_requires_evidence_before_calling_a_change_verified(monkeypatch, engine):
    replies = iter([
        '✿FUNCTION✿: write_file ✿ARGS✿: {"filename":"x.txt","content":"x"}',
        "Done.",
        "Done.",
    ])
    state = {}

    monkeypatch.setattr(engine, "_create_completion_with_fallback",
                        lambda *args, **kwargs: _response(next(replies)))

    def write_then_report(name, arguments, depth=0):
        engine.note_mutation("test write")
        return "written"

    monkeypatch.setattr(engine, "exec_tool", write_then_report)
    answer = engine.run_agent(
        [{"role": "user", "content": "write a file"}], max_turns=3,
        tools_def=[], allowed_tools={"write_file"}, trajectory_state=state,
    )

    assert "Verification note:" in answer
    assert state["verification"] == "pending"


def test_readonly_pipeline_does_not_look_like_a_mutation(engine):
    assert engine._readonly_shell("find . -type f | head -50")
    assert not engine._readonly_shell("find . -delete | head -50")
    assert engine._readonly_shell("git -C /repo status")


def test_trajectory_upgrades_legacy_state_with_workspace_and_checklist():
    state = {"version": 1, "goal": "fix it", "operations": []}

    trajectory = TrajectoryState(state, "fix it", 1, workspace="/repo")

    assert state["version"] == 2
    assert state["workspace"] == "/repo"
    assert [step["status"] for step in state["checklist"]] == ["pending", "pending", "not_needed"]
    operation = trajectory.before_tool("read_text")
    trajectory.after_tool(operation, "ok", failed=False, blocked=False, mutated=False)
    assert "Workspace: use relative paths from the project workspace" in trajectory.prompt()


def test_trajectory_stalls_after_two_setbacks_and_keeps_evidence():
    trajectory = TrajectoryState({}, "repair it", 1, workspace="/repo")
    first = trajectory.before_tool("read_text")
    trajectory.after_tool(first, "Error: missing", failed=True, blocked=False, mutated=False)
    second = trajectory.before_tool("execute_shell")
    trajectory.after_tool(second, "Error: unavailable", failed=True, blocked=False, mutated=False)

    assert trajectory.needs_replan()
    assert trajectory.request_replan()
    assert "different safe tool" in trajectory.prompt()

    proof = trajectory.before_tool("execute_shell")
    trajectory.after_tool(proof, "3 passed", failed=False, blocked=False,
                          mutated=True, verdict="passed")
    assert trajectory.data["verification"] == "verified"
    assert trajectory.data["evidence"][-1]["result"] == "3 passed"


def test_read_back_is_inspection_evidence_not_an_automated_pass():
    trajectory = TrajectoryState({}, "write it", 1)
    write = trajectory.before_tool("write_file")
    trajectory.after_tool(write, "written", failed=False, blocked=False, mutated=True)
    read = trajectory.before_tool("read_text")
    trajectory.after_tool(read, "Read 1 line", failed=False, blocked=False, mutated=False)

    assert trajectory.data["verification"] == "inspected"
    assert not trajectory.needs_verification()
    assert "no automated verification" in trajectory.review()


def test_successful_retry_clears_the_same_tools_unresolved_note():
    trajectory = TrajectoryState({}, "review a pull request", 1)
    failed = trajectory.before_tool("review_code")
    trajectory.after_tool(failed, "Error: malformed URL", failed=True, blocked=False,
                          mutated=False)
    retry = trajectory.before_tool("review_code")
    trajectory.after_tool(retry, '{"status":"complete"}', failed=False, blocked=False,
                          mutated=False)

    assert trajectory.review() == ""


def test_main_loop_requests_one_different_approach_after_repeated_failures(monkeypatch, engine):
    replies = iter([
        '✿FUNCTION✿: read_text ✿ARGS✿: {"filename":"missing-a.txt"}',
        '✿FUNCTION✿: read_text ✿ARGS✿: {"filename":"missing-b.txt"}',
        "I cannot continue.",
        "The two files are unavailable; I would ask for their correct paths next.",
    ])
    messages = [{"role": "user", "content": "inspect two files"}]
    monkeypatch.setattr(engine, "_create_completion_with_fallback",
                        lambda *args, **kwargs: _response(next(replies)))
    monkeypatch.setattr(engine, "exec_tool", lambda *args, **kwargs: "Error: missing file")

    answer = engine.run_agent(messages, max_turns=4, tools_def=[], allowed_tools={"read_text"},
                              trajectory_state={})

    assert "correct paths next" in answer
    assert any("Do not repeat them" in str(message["content"]) for message in messages)
    assert "Unresolved tool work remains" in answer


def test_landed_paths_name_the_place_a_diverted_write_actually_is():
    state = TrajectoryState({}, goal="g", message_count=1)
    state.data["written_paths"] = ["/w/outputs/watch.py", "/w/artifacts/notes.md"]
    state.data["redirected_writes"] = [["/w/outputs/watch.py", "/w/artifacts/outputs/watch.py"]]
    assert state.landed_paths() == ["/w/artifacts/outputs/watch.py", "/w/artifacts/notes.md"]
    assert state.written_paths() == ["/w/outputs/watch.py", "/w/artifacts/notes.md"], "the record itself is unchanged"


def test_editing_a_test_file_this_run_created_is_not_editing_an_existing_one_even_when_it_was_diverted():
    state = TrajectoryState({}, goal="g", message_count=1)
    state.data["redirected_writes"] = [["/w/test_board.py", "/w/artifacts/test_board.py"]]
    state._note_test_edit("/w/test_board.py", existed=False)         # created under the name it was asked for
    state._note_test_edit("/w/artifacts/test_board.py", existed=True)   # edited under the name it landed at
    assert state.edited_existing_tests() == []


def test_editing_a_test_file_that_was_there_before_the_run_is_still_noted():
    state = TrajectoryState({}, goal="g", message_count=1)
    state._note_test_edit("/w/tests/test_old.py", existed=True)
    assert state.edited_existing_tests() == ["/w/tests/test_old.py"]
