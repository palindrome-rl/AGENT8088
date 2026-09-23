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

    def fake_exec(args, depth=0, history=None, from_user=False):
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

    def fake_exec(args, depth=0, history=None, from_user=False):
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


def _loop(cli, monkeypatch, answers, rest="joker"):
    profiles = {"joker": {"description": "test", "tools": [], "max_turns": 4,
                          "model": "inherit", "system_prompt": "You tell jokes"}}
    monkeypatch.setattr(cli.A, "SUBAGENT_SPECS", profiles)
    seen = []

    def fake_exec(args, depth=0, history=None, from_user=False):
        seen.append(args["task"])
        return f"[subagent:joker] did {args['task']}"

    monkeypatch.setattr(cli.A, "_exec_subagent", fake_exec)
    monkeypatch.setattr(cli, "render_answer", lambda a: None)
    feed = iter(answers)
    monkeypatch.setattr(cli.console, "input", lambda prompt="": next(feed))
    cli.cmd_agent(rest)
    return seen


def test_a_repl_command_typed_in_the_loop_is_not_sent_as_a_task(cli, monkeypatch, capsys):
    """Live, /status at `task for explore >` went to the sub-agent, which spent
    seven shell calls investigating it."""
    seen = _loop(cli, monkeypatch, ["/status", "tell me a joke", "/quit"])

    assert seen == ["tell me a joke"]
    out = capsys.readouterr().out
    assert "/status" in out and "/quit" in out


def test_a_task_that_starts_with_a_path_is_still_a_task(cli, monkeypatch):
    seen = _loop(cli, monkeypatch, ["/usr/local/bin -- is it on PATH?", "/quit"])

    assert seen == ["/usr/local/bin -- is it on PATH?"]


def test_a_mistyped_agent_name_gets_a_hint(cli, monkeypatch, capsys):
    """`/agent wbe open ...` made the whole line the task and opened the
    picker; with piped input the picker cancels and nothing said why."""
    seen = _loop(cli, monkeypatch, [], rest="jokre tell me a joke")

    assert seen == []
    assert "did you mean" in capsys.readouterr().out


def test_a_task_that_resembles_an_agent_name_still_runs(cli, monkeypatch):
    """"/agent joke about cats" is a task for the picker, not a typo to refuse."""
    monkeypatch.setattr(cli, "select_agent", lambda profiles: "joker")
    seen = _loop(cli, monkeypatch, ["/quit"], rest="joke about cats")

    assert seen == ["joke about cats"]


def test_quit_leaves_the_main_prompt_as_well_as_the_agent_loop(cli):
    """`/agent` says it stays "until /quit"; the main prompt answered /quit with
    "unknown command"."""
    for word in ("/quit", "quit", "/exit", "exit"):
        assert word in cli._EXIT_WORDS
