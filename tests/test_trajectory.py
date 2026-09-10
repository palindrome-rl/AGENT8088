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
