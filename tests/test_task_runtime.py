import json
import threading

from agent8088.task_runtime import TaskStore, run_task


def test_checkpoint_and_resume(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    seen = []
    progress = []

    def agent(messages, **_):
        seen.append(len(messages))
        messages.append({"role": "assistant", "content": "progress"})
        return "progress"

    row = run_task("make a report", agent, store=store, workspace=tmp_path,
                   max_slices=2, slice_turns=1,
                   on_slice=lambda task, event: progress.append((task["slice_no"], event)))
    assert row["state"] == "paused"
    assert row["slice_no"] == 2
    assert seen == [1, 3]
    assert progress == [(1, "running"), (1, "checkpointed"), (2, "running"),
                        (2, "checkpointed"), (2, "paused")]
    assert json.loads(row["messages_json"])

    op = store.start_operation(row["id"], "write_file", {"api_key": "do-not-store"})
    store.finish_operation(op, "ok")
    stored = store.db.execute("SELECT args_json FROM task_operations WHERE id=?", (op,)).fetchone()[0]
    assert "do-not-store" not in stored
    assert "redacted" in stored
    assert store.recent_operations(row["id"])[-1]["id"] == op


def test_cancelled_task_does_not_resume(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    task_id = store.create("stop", tmp_path, [{"role": "user", "content": "stop"}])
    assert store.resolve(task_id[:12])["id"] == task_id
    store.cancel(task_id)
    assert store.list() == []
    assert [task["id"] for task in store.list(include_cancelled=True)] == [task_id]
    assert store.resolve(task_id[:12])["state"] == "cancelled"
    row = run_task("ignored", lambda *_args, **_kwargs: "should not run",
                   store=store, workspace=tmp_path, task_id=task_id)
    assert row["state"] == "cancelled"


def test_end_during_worker_keeps_task_until_final_checkpoint(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    task_id = store.create("review", tmp_path, [{"role": "user", "content": "review"}])

    def agent(_messages, **_kwargs):
        assert store.cancel(task_id)["state"] == "cancelling"
        assert not store.delete(task_id)
        return "done"

    row = run_task("review", agent, store=store, workspace=tmp_path, task_id=task_id)
    assert row["state"] == "cancelled"
    assert store.get(task_id)["last_answer"] == "done"
    assert store.db.execute(
        "SELECT COUNT(*) FROM task_events WHERE task_id=?", (task_id,)).fetchone()[0] > 0
    assert store.delete(task_id)
    assert store.db.execute(
        "SELECT COUNT(*) FROM task_events WHERE task_id=?", (task_id,)).fetchone()[0] == 0


def test_concurrent_resume_claims_only_one_worker(tmp_path):
    path = tmp_path / "tasks.db"
    first_store = TaskStore(path)
    task_id = first_store.create("resume", tmp_path, [{"role": "user", "content": "resume"}])
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def agent(_messages, **_kwargs):
        calls.append(1)
        entered.set()
        assert release.wait(5)
        return "TASK_COMPLETE"

    def run_first_worker():
        worker_store = TaskStore(path)
        try:
            run_task("resume", agent, store=worker_store,
                     workspace=tmp_path, task_id=task_id)
        finally:
            worker_store.close()

    worker = threading.Thread(target=run_first_worker)
    worker.start()
    assert entered.wait(5)
    second_store = TaskStore(path)
    try:
        row = run_task("resume", agent, store=second_store, workspace=tmp_path,
                       task_id=task_id)
        assert row["state"] == "running"
        assert calls == [1]
    finally:
        release.set()
        worker.join(5)
        second_store.close()
        first_store.close()
    assert not worker.is_alive()
    assert calls == [1]


def test_concurrent_resume_cannot_claim_between_slices(tmp_path):
    path = tmp_path / "tasks.db"
    initial_store = TaskStore(path)
    task_id = initial_store.create("resume", tmp_path, [{"role": "user", "content": "resume"}])
    checkpointed = threading.Event()
    release = threading.Event()
    calls = []

    def agent(_messages, **_kwargs):
        calls.append(1)
        return "TASK_PROGRESS"

    def run_first_worker():
        worker_store = TaskStore(path)
        try:
            original_update = worker_store.update

            def hold_queued(task, **fields):
                original_update(task, **fields)
                if fields.get("state") == "queued":
                    checkpointed.set()
                    release.wait(5)

            worker_store.update = hold_queued
            run_task("resume", agent, store=worker_store, workspace=tmp_path,
                     task_id=task_id, max_slices=1)
        finally:
            worker_store.close()

    worker = threading.Thread(target=run_first_worker)
    worker.start()
    assert checkpointed.wait(5)
    second_store = TaskStore(path)
    try:
        row = run_task("resume", agent, store=second_store, workspace=tmp_path,
                       task_id=task_id)
        assert row["state"] == "queued"
        assert calls == [1]
    finally:
        release.set()
        worker.join(5)
        second_store.close()
        initial_store.close()
    assert not worker.is_alive()
    assert calls == [1]


def test_windows_worker_liveness_uses_process_query_not_kill_zero(monkeypatch):
    import os
    import pytest
    from agent8088.task_runtime import TaskStore

    if os.name != "nt":
        pytest.skip("Windows process query semantics")
    monkeypatch.setattr(os, "kill", lambda *_args: pytest.fail("os.kill must not probe Windows PIDs"))
    assert TaskStore.process_is_alive(os.getpid())
    assert not TaskStore.process_is_alive(0x7FFFFFFF)


def test_resuming_a_task_does_not_pause_another_running_task(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    first = store.create("resume me", tmp_path, [{"role": "user", "content": "first"}])
    other = store.create("leave me alone", tmp_path, [{"role": "user", "content": "second"}])
    store.update(first, state="running")
    store.update(other, state="running")

    run_task("ignored", lambda *_args, **_kwargs: "unused", store=store,
             workspace=tmp_path, task_id=first, max_slices=0)

    assert store.get(other)["state"] == "running"


def test_task_tells_the_agent_how_to_finish(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    prompts = []
    answers = iter(("still working TASK_PROGRESS", "done TASK_COMPLETE"))

    def agent(messages, **_):
        prompts.append(messages[-1]["content"])
        return next(answers)

    row = run_task("checkout", agent, store=store, workspace=tmp_path,
                   max_slices=2, slice_turns=1)

    assert row["state"] == "completed"
    assert all("TASK_COMPLETE" in prompt for prompt in prompts)


def test_a_slice_does_not_get_a_growing_turn_budget(tmp_path):
    """run_task already grants continuation by re-running; slices stay fixed."""
    from agent8088.task_runtime import TaskStore, run_task

    store = TaskStore(tmp_path / "tasks.db")
    seen = []

    def agent(messages, **kwargs):
        seen.append(kwargs.get("dynamic_turns"))
        return "TASK_COMPLETE"

    run_task("do it", agent, store=store, workspace=tmp_path,
             max_slices=1, slice_turns=3)

    assert seen == [False], f"slice ran with dynamic_turns={seen}"


def test_task_checkpoints_controller_state_for_a_later_resume(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")

    def agent(_messages, *, trajectory_state, on_trajectory_state, **_kwargs):
        trajectory_state.update({"version": 2, "goal": "durable", "operations": [{"tool": "read_text", "status": "done"}]})
        on_trajectory_state(trajectory_state)
        return "TASK_COMPLETE"

    row = run_task("durable", agent, store=store, workspace=tmp_path, max_slices=1)

    assert row["state"] == "completed"
    assert json.loads(row["trajectory_json"])["operations"][-1]["tool"] == "read_text"
