"""Eval mode: one switch that leaves out the steps that cannot change what a run leaves on disk.

A run graded on its final state (an experiment, a measured comparison) gains nothing from a final summary, an
optional look at a page, or a screenshot nobody asked for; each costs time or context. `run_mode=eval` (or
--eval, AGENT8088_RUN_MODE=eval, /eval on) turns those off and keeps every check whose result can lead to a fix.
`full`, the default, changes nothing. Each run records its mode in its trace, so results from the two are never mixed.
"""
from pathlib import Path

import pytest

from tests.test_completion_pipeline import Script, _call, eng, failing_then_ok  # noqa: F401


# ----------------------------------------------------------------------------- the switch

@pytest.mark.parametrize("env, config, expected", [
    ("", "", "full"),
    ("", "eval", "eval"),
    ("eval", "full", "eval"),        # the environment wins: a script can switch one run
    ("EVAL", "", "eval"),
    ("benchmark", "", "full"),       # an unknown value falls back to full
])
def test_the_mode_comes_from_the_environment_then_the_config(engine, env, config, expected):
    assert engine._initial_run_mode({"AGENT8088_RUN_MODE": env}, {"run_mode": config}) == expected


def test_the_mode_can_be_switched_for_the_session(engine, monkeypatch):
    monkeypatch.setattr(engine, "RUN_MODE", "full")
    assert engine.set_run_mode("eval") == "eval" and engine.eval_mode()
    assert engine.set_run_mode("full") == "full" and not engine.eval_mode()
    with pytest.raises(ValueError, match="full or eval"):
        engine.set_run_mode("fast")


def test_each_run_records_its_mode_in_the_trace(eng, monkeypatch):
    for mode in ("full", "eval"):
        monkeypatch.setattr(eng, "RUN_MODE", mode)
        Script(eng, monkeypatch, ["Done."])
        trace = []
        eng.run_agent([{"role": "user", "content": "say hi"}], max_turns=3, tools_def=[],
                      allowed_tools=set(), trace=trace)
        assert [s["mode"] for s in trace if s.get("type") == "run_mode"] == [mode]


# ----------------------------------------------------------------------------- what eval mode leaves out

def _near_the_end(eng, monkeypatch):
    monkeypatch.setattr(eng, "DISPOSABLE_CONTAINER", False)
    budget = eng._TurnBudget(max_seconds=1000)
    budget.started -= 950
    script = Script(eng, monkeypatch, [_call("execute_shell", command="echo a"),
                                       _call("execute_shell", command="echo b"), "Done."],
                    tool=failing_then_ok(["a", "b"]))
    eng.run_agent([{"role": "user", "content": "say hi"}], max_turns=6, tools_def=[],
                  allowed_tools={"execute_shell"}, budget=budget)
    return script


def test_full_mode_still_asks_for_the_answer_in_the_last_stretch(eng, monkeypatch):
    monkeypatch.setattr(eng, "RUN_MODE", "full")
    assert _near_the_end(eng, monkeypatch).nudges("give your final answer now")


def test_eval_mode_keeps_working_instead_of_writing_the_answer(eng, monkeypatch):
    """Graded on the final state, the last minutes are worth more as work than as a written summary."""
    monkeypatch.setattr(eng, "RUN_MODE", "eval")
    assert not _near_the_end(eng, monkeypatch).nudges("give your final answer now")


def test_eval_mode_skips_the_optional_look_at_a_page(engine, monkeypatch, tmp_path):
    from agent8088.trajectory import TrajectoryState
    monkeypatch.setattr(engine, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(engine, "ALLOWED_PATHS", [tmp_path])
    monkeypatch.setattr(engine, "BLOCKED_PATHS", [])
    monkeypatch.setattr(engine, "ARTIFACTS_ROOT", tmp_path / "artifacts")
    monkeypatch.setattr(engine, "COMPLETION_VISUAL_LOOK", True)
    request = "Build a polished landing page with a clear layout and a warm colour palette."
    state = TrajectoryState({}, goal=request, message_count=1, workspace=str(tmp_path))
    (tmp_path / "index.html").write_text("<!doctype html><h1>Hi</h1>", encoding="utf-8")
    op = state.before_tool("write_file")
    state.after_tool(op, "ok", failed=False, blocked=False, mutated=True, verdict="unknown",
                     path=str(tmp_path / "index.html"))
    monkeypatch.setattr(engine, "RUN_MODE", "eval")
    assert engine._visual_look_step(state, state.landed_paths(), request) is None
    monkeypatch.setattr(engine, "RUN_MODE", "full")
    assert engine._visual_look_step(state, state.landed_paths(), request) is not None


class _Shot:
    def __init__(self, label, path):
        self.label, self.path = label, path


class _Report:
    def __init__(self, shots, screenshot, passed):
        self.shots, self.screenshot, self.passed, self.error = shots, screenshot, passed, ""


def test_eval_mode_attaches_only_the_screenshots_a_test_asked_for(engine, monkeypatch, tmp_path):
    asked, last = tmp_path / "asked.png", tmp_path / "last.png"
    asked.write_bytes(b"png")
    last.write_bytes(b"png")
    report = _Report([_Shot("after the jump", str(asked))], str(last), passed=False)
    monkeypatch.setattr(engine, "RUN_MODE", "full")
    assert [label for label, _ in engine._browser_test_images(report)] == [
        "after the jump", "the state when the test stopped"]
    monkeypatch.setattr(engine, "RUN_MODE", "eval")
    assert [label for label, _ in engine._browser_test_images(report)] == ["after the jump"]


# ----------------------------------------------------------------------------- the command line

def test_the_eval_flag_and_command(engine, monkeypatch):
    from agent8088 import cli
    monkeypatch.setattr(cli.A, "RUN_MODE", "full")
    cli.cmd_eval("on")
    assert cli.A.eval_mode()
    cli.cmd_eval("off")
    assert not cli.A.eval_mode()
    assert "--eval" in Path(cli.__file__).read_text(encoding="utf-8")
