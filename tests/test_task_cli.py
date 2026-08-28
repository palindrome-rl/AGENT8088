from agent8088 import cli
from agent8088 import task_runtime


def test_task_forwards_permission_requests_to_the_cli(monkeypatch):
    seen = []

    class Store:
        def __init__(self, *_args):
            pass

        def close(self):
            pass

    def fake_run_task(_goal, agent, **_kwargs):
        return {"id": "a" * 32, "state": "completed", "slice_no": 1,
                "last_answer": agent([])}

    def fake_run_agent(_messages, **kwargs):
        assert kwargs["on_escalation"]("write_text", "ESCALATION_REQUEST\x1f"
                                        "write_text\x1fwrite\x1fwork\x1ftest")
        return "TASK_COMPLETE"

    monkeypatch.setattr(task_runtime, "TaskStore", Store)
    monkeypatch.setattr(task_runtime, "run_task", fake_run_task)
    monkeypatch.setattr(cli.A, "run_agent", fake_run_agent)
    monkeypatch.setattr(cli, "_handle_escalation", lambda result: seen.append(result) or True)

    cli.cmd_task("start write a note")

    assert seen == ["ESCALATION_REQUEST\x1fwrite_text\x1fwrite\x1fwork\x1ftest"]
