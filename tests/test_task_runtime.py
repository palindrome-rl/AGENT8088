import json

from agent8088.task_runtime import TaskStore, run_task


def test_checkpoint_and_resume(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    seen = []

    def agent(messages, **_):
        seen.append(len(messages))
        messages.append({"role": "assistant", "content": "progress"})
        return "progress"

    row = run_task("make a report", agent, store=store, workspace=tmp_path,
                   max_slices=2, slice_turns=1)
    assert row["state"] == "paused"
    assert row["slice_no"] == 2
    assert seen == [1, 3]
    assert json.loads(row["messages_json"])

    op = store.start_operation(row["id"], "write_file", {"api_key": "do-not-store"})
    store.finish_operation(op, "ok")
    stored = store.db.execute("SELECT args_json FROM task_operations WHERE id=?", (op,)).fetchone()[0]
    assert "do-not-store" not in stored
    assert "redacted" in stored
