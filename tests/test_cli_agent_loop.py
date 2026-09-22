import importlib

import pytest


@pytest.fixture
def cli(engine):
    from agent8088 import cli as module

    return importlib.reload(module)


def test_agent_loop_stays_until_quit_and_reuses_history(cli, monkeypatch, capsys):
    """The task loop keeps reading tasks until /quit, and every task shares
    one history list so the sub-agent remembers the earlier exchanges."""
    profiles = {"joker": {"description": "test", "tools": [], "max_turns": 4,
                          "model": "inherit", "system_prompt": "You tell jokes"}}
    monkeypatch.setattr(cli.A, "SUBAGENT_SPECS", profiles)
    seen = []

    def fake_exec(args, depth=0, history=None):
        seen.append((args["task"], history))
        if history is not None:
            history.append({"role": "user", "content": args["task"]})
        return f"[subagent:joker] did {args['task']}"

    monkeypatch.setattr(cli.A, "_exec_subagent", fake_exec)
    monkeypatch.setattr(cli, "render_answer", lambda a: None)
    answers = iter(["tell me a joke", "/quit"])

    def fake_input(prompt=""):
        return next(answers)

    monkeypatch.setattr(cli.console, "input", fake_input)
    cli.cmd_agent("joker")

    assert len(seen) == 1  # second read was /quit, not a task
    assert seen[0][0] == "tell me a joke"
    assert isinstance(seen[0][1], list)  # conversation list threaded through


def test_agent_loop_multiple_tasks_share_one_history(cli, monkeypatch):
    profiles = {"joker": {"description": "test", "tools": [], "max_turns": 4,
                          "model": "inherit", "system_prompt": "You tell jokes"}}
    monkeypatch.setattr(cli.A, "SUBAGENT_SPECS", profiles)
    seen = []

    def fake_exec(args, depth=0, history=None):
        seen.append((args["task"], history))
        if history is not None:
            history.append({"role": "user", "content": args["task"]})
        return f"[subagent:joker] did {args['task']}"

    monkeypatch.setattr(cli.A, "_exec_subagent", fake_exec)
    monkeypatch.setattr(cli, "render_answer", lambda a: None)
    answers = iter(["first task", "second task", ""])

    monkeypatch.setattr(cli.console, "input", lambda prompt="": next(answers))
    cli.cmd_agent("joker")

    assert [t for t, _ in seen] == ["first task", "second task"]
    assert seen[0][1] is seen[1][1]  # same list: memory carries across tasks
