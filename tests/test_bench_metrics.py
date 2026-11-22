"""The benchmark's numbers: what a run's trace says, and how it compares with the grader.

The benchmark is only useful if its numbers are right, so these tests pin how a
trace export becomes tokens, compactions, recoveries and a harness verdict, and
how that verdict is scored against the independent grader.
"""
import json
import sys
from pathlib import Path

import pytest

BENCH = Path(__file__).resolve().parent.parent / "bench"
sys.path.insert(0, str(BENCH))

import benchlib


def _trace(steps, usage=(1000, 200), extra_turns=()):
    turns = list(extra_turns) + [{"input": "task", "steps": steps, "seconds": 12.5,
                                  "usage": {"input_tokens": usage[0], "output_tokens": usage[1]}}]
    return {"version": 2, "model": "m", "trace": turns}


def _answer(text, incomplete=False):
    return {"turn": 9, "type": "final_answer", "content": text, "incomplete": incomplete}


# ----------------------------------------------------------------- the harness verdict

@pytest.mark.parametrize("answer,incomplete,end,expected", [
    ("Done. Verification: tested. A test run passed after the last change.", False, "answered", "verified"),
    ("Done. Verification: exercised. The result was run.", False, "answered", "verified"),
    ("Done. Verification: inspected. Files read back.", False, "answered", "done_unchecked"),
    ("Done, nothing was run.", False, "answered", "done_unchecked"),
    ("NOT VERIFIED: the latest check failed", False, "answered", "not_verified"),
    ("Done. Verification: tested.", True, "checks_failed", "not_verified"),
    ("", False, "time_budget", "no_answer"),
    ("", False, "model_error", "no_answer"),
])
def test_the_harness_verdict_comes_from_the_final_answer(answer, incomplete, end, expected):
    steps = [_answer(answer, incomplete)] if answer or incomplete else []
    assert benchlib.summarize_trace(_trace(steps), end_reason=end)["harness_verdict"] == expected


def test_a_failed_verification_level_is_not_verified_even_without_the_prefix():
    steps = [_answer("Here is the app. Verification: failed. A check failed and has not passed since.")]
    assert benchlib.summarize_trace(_trace(steps), end_reason="answered")["harness_verdict"] == "not_verified"


def test_the_verification_level_is_recorded():
    steps = [_answer("Done. Verification: exercised. The result was run.")]
    assert benchlib.summarize_trace(_trace(steps), end_reason="answered")["verification_level"] == "exercised"


# ----------------------------------------------------------------- run facts from the trace

def test_tokens_are_summed_over_every_turn():
    earlier = {"input": "x", "steps": [], "usage": {"input_tokens": 50, "output_tokens": 5}}
    summary = benchlib.summarize_trace(_trace([_answer("ok")], usage=(1000, 200), extra_turns=[earlier]),
                                       end_reason="answered")
    assert (summary["input_tokens"], summary["output_tokens"]) == (1050, 205)


def test_tokens_are_marked_estimated_when_the_server_sent_no_usage():
    # A streamed reply without a usage object counts no input and estimates the output from its length.
    estimated = benchlib.summarize_trace(_trace([_answer("ok")], usage=(0, 5000)), end_reason="answered")
    counted = benchlib.summarize_trace(_trace([_answer("ok")], usage=(9000, 5000)), end_reason="answered")
    assert estimated["tokens_estimated"] is True
    assert counted["tokens_estimated"] is False


def test_runs_ask_the_server_for_token_usage(tmp_path):
    import run as bench_run

    task = {"budget_sec": 900, "config": {}}
    config = bench_run.run_config({"config": {"model_telemetry": "0"}}, task, tmp_path)
    # Streamed replies carry usage only when model telemetry asks for it.
    assert config["model_telemetry"] == "1"
    assert config["project_root"] == str(tmp_path)
    assert config["show_trace"] == "1"


def test_the_agent_does_not_inherit_the_runners_own_venv(tmp_path, monkeypatch):
    # The runner is often started with `uv run`; the agent's shell must see the
    # machine's own tools, not the benchmark's virtual environment.
    import os

    import run as bench_run

    runner_venv = tmp_path / "bench-venv"
    bin_dir = runner_venv / ("Scripts" if os.name == "nt" else "bin")
    other = tmp_path / "tools"
    monkeypatch.setenv("PATH", os.pathsep.join([str(bin_dir), str(other)]))
    monkeypatch.setenv("VIRTUAL_ENV", str(runner_venv))
    monkeypatch.setenv("AGENT8088_HOME", "/somewhere/else")
    monkeypatch.setenv("MY_API_KEY", "secret")
    env = bench_run.agent_env({"env_keys": ["MY_API_KEY"]}, runner_venv=runner_venv)
    assert "VIRTUAL_ENV" not in env
    assert str(bin_dir) not in env["PATH"].split(os.pathsep)
    assert str(other) in env["PATH"].split(os.pathsep)
    assert "AGENT8088_HOME" not in env
    assert "MY_API_KEY" not in env


def test_a_turn_without_usage_does_not_break_the_sum():
    turn = {"input": "x", "steps": [], "usage": None}
    summary = benchlib.summarize_trace(_trace([_answer("ok")], extra_turns=[turn]), end_reason="answered")
    assert summary["input_tokens"] == 1000


def test_compactions_and_failed_compactions_are_counted():
    steps = [{"type": "compaction", "ok": True}, {"type": "compaction", "ok": False},
             {"type": "compaction", "ok": True}, _answer("ok")]
    summary = benchlib.summarize_trace(_trace(steps), end_reason="answered")
    assert (summary["compactions"], summary["compactions_failed"]) == (3, 1)


def test_problems_and_recoveries_are_counted_by_kind():
    steps = [{"type": "length_cutoff"}, {"type": "length_cutoff"}, {"type": "cutoff_write_salvaged"},
             {"type": "context_overflow_recovery"}, {"type": "tool_arg_parse_error"}, _answer("ok")]
    summary = benchlib.summarize_trace(_trace(steps), end_reason="answered")
    assert summary["problems"] == {"length_cutoff": 2, "tool_arg_parse_error": 1}
    assert summary["recoveries"] == {"cutoff_write_salvaged": 1, "context_overflow_recovery": 1}


def test_tool_calls_are_counted():
    steps = [{"type": "tool_calls", "tools": [{"name": "a"}, {"name": "b"}]},
             {"type": "tool_calls", "tools": [{"name": "c"}]}, _answer("ok")]
    assert benchlib.summarize_trace(_trace(steps), end_reason="answered")["tool_calls"] == 3


def test_an_empty_or_missing_trace_still_gives_a_summary():
    summary = benchlib.summarize_trace(None, end_reason="unknown")
    assert summary["harness_verdict"] == "no_answer"
    assert summary["input_tokens"] == 0


# ----------------------------------------------------------------- cost

def test_cost_uses_the_profile_prices_per_million_tokens():
    assert benchlib.cost_usd(2_000_000, 500_000, {"price_in": 0.5, "price_out": 2.0}) == pytest.approx(2.0)


def test_cost_is_none_when_the_profile_has_no_prices():
    assert benchlib.cost_usd(10, 10, {}) is None


# ----------------------------------------------------------------- scoring against the grader

def _result(verdict, passed, task="t1", model="m1", **extra):
    return {"task": task, "model": model, "harness_verdict": verdict,
            "grader": {"passed": passed, "score": 1.0 if passed else 0.0}, **extra}


def test_false_done_is_a_run_the_harness_called_done_that_the_grader_failed():
    assert benchlib.outcome(_result("verified", False)) == "false_done"
    assert benchlib.outcome(_result("done_unchecked", False)) == "false_done"


def test_false_not_verified_is_a_run_the_harness_doubted_that_the_grader_passed():
    assert benchlib.outcome(_result("not_verified", True)) == "false_not_verified"


def test_agreeing_verdicts_are_correct():
    assert benchlib.outcome(_result("verified", True)) == "correct"
    assert benchlib.outcome(_result("not_verified", False)) == "correct"



def test_a_run_that_never_answered_is_its_own_outcome_whatever_the_grader_found():
    # The user was told nothing, so the run is neither a correct verdict nor a false one.
    assert benchlib.outcome(_result("no_answer", False)) == "no_answer"
    assert benchlib.outcome(_result("no_answer", True)) == "no_answer"


def test_the_summary_counts_runs_without_an_answer():
    summary = benchlib.aggregate([_result("no_answer", True), _result("verified", True)])
    assert summary["overall"]["no_answer_rate"] == pytest.approx(0.5)
    # The grader still decides success.
    assert summary["overall"]["success_rate"] == pytest.approx(1.0)


def test_a_run_whose_grader_did_not_run_is_not_scored():
    result = _result("verified", True)
    result["grader"] = {"error": "grader crashed"}
    assert benchlib.outcome(result) == "ungraded"


def test_the_summary_reports_rates_per_task_and_model_and_overall():
    results = [
        _result("verified", True, input_tokens=100, output_tokens=10, elapsed_sec=60, compactions=0),
        _result("verified", False, input_tokens=100, output_tokens=10, elapsed_sec=120, compactions=1),
        _result("not_verified", True, input_tokens=100, output_tokens=10, elapsed_sec=30, compactions=2),
        _result("not_verified", False, input_tokens=100, output_tokens=10, elapsed_sec=30, compactions=0),
        _result("verified", True, task="t2", input_tokens=50, output_tokens=5, elapsed_sec=10, compactions=0),
    ]
    summary = benchlib.aggregate(results)
    t1 = summary["groups"]["t1 | m1"]
    assert t1["runs"] == 4
    assert t1["success_rate"] == pytest.approx(0.5)
    # false-done is over the runs the harness called done, false not-verified over the runs it doubted.
    assert t1["false_done_rate"] == pytest.approx(0.5)
    assert t1["false_not_verified_rate"] == pytest.approx(0.5)
    assert t1["success_after_compaction"] == "1/2"
    assert summary["overall"]["runs"] == 5
    assert summary["overall"]["success_rate"] == pytest.approx(0.6)
    assert summary["overall"]["input_tokens"] == 450


def test_rates_are_none_when_there_is_nothing_to_divide_by():
    summary = benchlib.aggregate([_result("verified", True)])
    assert summary["overall"]["false_not_verified_rate"] is None


# ----------------------------------------------------------------- tasks

def test_every_shipped_task_loads_and_names_files_that_exist():
    tasks = benchlib.discover_tasks(BENCH / "tasks")
    assert tasks, "the benchmark ships at least one task"
    for task in tasks:
        assert (task["dir"] / task["prompt"]).is_file()
        assert (task["dir"] / task["grader"]).is_file()
        assert task["budget_sec"] > 0
        assert task["kind"] in ("sandbox", "long")


def test_a_task_missing_a_required_field_is_refused(tmp_path):
    (tmp_path / "task.json").write_text(json.dumps({"id": "x", "prompt": "prompt.md"}), encoding="utf-8")
    with pytest.raises(ValueError, match="grader"):
        benchlib.load_task(tmp_path)


def test_the_task_id_must_match_its_folder(tmp_path):
    folder = tmp_path / "right"
    folder.mkdir()
    (folder / "task.json").write_text(json.dumps(
        {"id": "wrong", "kind": "sandbox", "prompt": "p.md", "grader": "g.py", "budget_sec": 60}), encoding="utf-8")
    with pytest.raises(ValueError, match="folder"):
        benchlib.load_task(folder)


# ----------------------------------------------------------------- wall clock

def test_wall_clock_is_shown_in_12_hour_time_with_the_zone():
    text = benchlib.clock("2026-11-21T00:09:03+05:00")
    assert text == "12:09 AM (+05:00)"


# ----------------------------------------------------------------- the sandbox the run used

@pytest.mark.parametrize("log,expected", [
    ("agent8088 ! sandbox using docker (native sandbox not installed) - commands run in a container", "docker"),
    ("... sandboxed shell and Python commands are refused ...", "none"),
    ("nothing about the sandbox, so the native one started", "native"),
])
def test_the_sandbox_backend_is_read_from_the_agent_log(log, expected):
    assert benchlib.sandbox_backend(log) == expected


def test_shared_runtime_folders_are_made_available_in_the_run_home(tmp_path):
    import run as bench_run

    source = tmp_path / "data"
    (source / "runtime" / "node_modules").mkdir(parents=True)
    (source / "runtime" / "node_modules" / "x.js").write_text("x", encoding="utf-8")
    (source / "node").mkdir()
    (source / "node" / "node.exe").write_text("n", encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    shared = bench_run.share_runtime({"shared_from": str(source)}, home)
    assert sorted(shared) == ["node", "runtime"]
    assert (home / "runtime" / "node_modules" / "x.js").read_text(encoding="utf-8") == "x"
    assert (home / "node" / "node.exe").read_text(encoding="utf-8") == "n"
    # The runtime is a copy, so a run cannot change the one it came from.
    (home / "runtime" / "node_modules" / "x.js").write_text("changed", encoding="utf-8")
    assert (source / "runtime" / "node_modules" / "x.js").read_text(encoding="utf-8") == "x"


def test_nothing_is_shared_without_a_source(tmp_path):
    import run as bench_run

    assert bench_run.share_runtime({}, tmp_path) == []


# -- the agent executable ------------------------------------------------------------------------

def test_a_relative_agent_path_becomes_absolute(tmp_path, monkeypatch):
    import run as bench_run
    exe = tmp_path / "bin" / "agent8088.exe"
    exe.parent.mkdir()
    exe.write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    resolved = bench_run.resolve_agent(str(Path("bin") / "agent8088.exe"))
    assert Path(resolved).is_absolute() and Path(resolved) == exe.resolve()


def test_a_windows_suffix_is_found(tmp_path, monkeypatch):
    import run as bench_run
    exe = tmp_path / "bin" / "agent8088.exe"
    exe.parent.mkdir()
    exe.write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert Path(bench_run.resolve_agent(str(Path("bin") / "agent8088"))) == exe.resolve()


def test_a_bare_name_is_looked_up_on_path(tmp_path, monkeypatch):
    import run as bench_run
    monkeypatch.setattr(bench_run.shutil, "which", lambda name: str(tmp_path / "found" / name))
    monkeypatch.chdir(tmp_path)
    assert bench_run.resolve_agent("agent8088") == str((tmp_path / "found" / "agent8088").resolve())


def test_a_missing_agent_stops_before_any_run(tmp_path, monkeypatch):
    import run as bench_run
    monkeypatch.setattr(bench_run.shutil, "which", lambda name: None)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit):
        bench_run.resolve_agent("agent8088")
    with pytest.raises(SystemExit):
        bench_run.resolve_agent(str(Path("missing") / "agent8088.exe"))
