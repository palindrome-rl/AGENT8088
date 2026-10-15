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


def _ran(state, command, verdict, status="done", tool="execute_shell"):
    op = {"tool": tool, "status": "running"}
    state.after_tool(op, "output", failed=status != "done", blocked=False, mutated=False,
                     verdict=verdict, command=command)


def test_a_command_that_only_looks_around_and_exits_non_zero_is_not_a_failed_check():
    state = TrajectoryState({}, goal="g", message_count=1)
    _ran(state, 'dir /B && for %F in (a.txt) do type %F', "failed")
    _ran(state, 'copy a.txt outputs\\a.txt', "failed")
    assert not state.check_failing()


def test_a_command_that_runs_a_check_and_fails_is_a_failed_check():
    for command in ("python test_board.py", "node logic_test.js", "pytest -q", "python -m py_compile app.py",
                    "npm run build", "python verify_outputs.py", "cd /d C:\\w && python e2e_check.py"):
        state = TrajectoryState({}, goal="g", message_count=1)
        _ran(state, command, "failed")
        assert state.check_failing(), command


def test_a_failure_of_a_dedicated_check_tool_is_still_a_failed_check():
    state = TrajectoryState({}, goal="g", message_count=1)
    _ran(state, "", "failed", tool="check_app")
    assert state.check_failing()


def test_the_command_that_failed_running_cleanly_later_settles_it_and_another_command_does_not():
    state = TrajectoryState({}, goal="g", message_count=1)
    _ran(state, "python test_board.py", "failed")
    _ran(state, "python other_script.py", "unknown")
    assert state.check_failing(), "a different command says nothing about the failed one"
    _ran(state, "python  test_board.py", "unknown")
    assert not state.check_failing() and state.failing_check_detail() == ""
