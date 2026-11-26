"""Tier 1 and the likely cause of a run.

A failed task is not always the harness's fault. The model can get the work wrong and the harness rightly say so; the
sandbox can stop a check; the model server can send nothing back. The report names who a run likely belongs to, and
only a wrong verdict or a missing answer is put on the harness.
"""
import json
import sys
from pathlib import Path

import pytest

BENCH = Path(__file__).resolve().parent.parent / "bench"
sys.path.insert(0, str(BENCH))

import benchlib


def _result(verdict, passed, answer="", **extra):
    grader = {"passed": passed, "score": 1.0 if passed else 0.5} if passed is not None else {"error": "no browser"}
    return {"harness_verdict": verdict, "grader": grader, "final_answer_head": answer, "tool_calls": 12, **extra}


@pytest.mark.parametrize("result, who", [
    (_result("verified", True), "none"),
    (_result("not_verified", False, "NOT VERIFIED: the latest check failed."), "model"),
    (_result("verified", False), "harness"),                                   # false done
    (_result("not_verified", True, "NOT VERIFIED: 2 part(s) of the request not addressed."), "harness"),
    (_result("not_verified", True, "NOT VERIFIED: ... the program 'node' is not available where the check ran"),
     "environment"),
    (_result("not_verified", False, "Not verified: no browser could be started here"), "environment"),
    (_result("not_verified", False, "I wasn't able to produce an answer to that."), "infra"),
    (_result("no_answer", True, tool_calls=0), "infra"),
    (_result("no_answer", True, stopped_by="watchdog"), "harness"),
    (_result("no_answer", False, problems={"length_cutoff": 3}), "model"),
    (_result("verified", None), "infra"),                                      # the grader could not run
])
def test_the_likely_cause_of_a_run(result, who):
    assert benchlib.likely_cause(result)[0] == who


def test_only_infra_runs_are_repeated():
    assert benchlib.is_infra(_result("verified", None))
    assert benchlib.is_infra(_result("no_answer", False, tool_calls=0))
    assert not benchlib.is_infra(_result("no_answer", False))                  # it worked, then gave no answer
    assert not benchlib.is_infra(_result("not_verified", False, "NOT VERIFIED: the latest check failed."))


def test_tier_1_names_real_tasks_and_runs_them_twice():
    tier = benchlib.load_tier("1")
    shipped = {task["id"] for task in benchlib.discover_tasks(BENCH / "tasks")}
    assert set(tier["tasks"]) <= shipped
    assert {"invoice-totals", "discount-rounding"} <= set(tier["tasks"])
    assert tier["trials"] == 2


def test_an_unknown_tier_is_refused():
    with pytest.raises(KeyError):
        benchlib.load_tier("9")


def _summary(false_done=0.0, no_answer=0.0, false_nv=0.2, score=0.9):
    return {"overall": {"false_done_rate": false_done, "no_answer_rate": no_answer,
                        "false_not_verified_rate": false_nv, "mean_score": score}}


def test_the_tier_bar():
    bar = benchlib.load_tier("1")["bar"]
    base = _summary(false_nv=0.4, score=0.85)
    assert all(ok for _r, ok, _d in benchlib.tier_check(_summary(), bar, base))
    failed = lambda summary: [rule for rule, ok, _d in benchlib.tier_check(summary, bar, base) if not ok]
    assert failed(_summary(false_done=0.1)) == ["no false done"]
    assert failed(_summary(no_answer=0.1)) == ["no run without an answer"]
    assert failed(_summary(false_nv=0.5)) == ["false not verified no higher than the baseline"]
    assert failed(_summary(score=0.7)) == ["grader score close to the baseline"]


def test_without_a_baseline_only_the_absolute_rules_are_checked():
    rules = [rule for rule, _ok, _d in benchlib.tier_check(_summary(), benchlib.load_tier("1")["bar"])]
    assert rules == ["no false done", "no run without an answer"]


def test_the_report_lists_runs_with_their_cause(tmp_path, capsys):
    import report
    rows = [dict(_result("verified", True), task="cart-page", started="2026-11-23T08:00:00+05:00"),
            dict(_result("verified", False), task="invoice-totals", started="2026-11-23T08:20:00+05:00")]
    path = tmp_path / "results.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    report.main([str(path), "--tier", "1"])
    out = capsys.readouterr().out
    assert "harness     false_done" in out and "invoice-totals" in out
    assert "result: NOT passed" in out


# ----------------------------------------------------------------- the added tasks

ADDED = {"npm-esbuild-bundle", "broken-test-green", "no-change-needed", "impossible-request", "sales-bar-chart"}


def test_tier_1_has_the_added_tasks():
    assert ADDED <= set(benchlib.load_tier("1")["tasks"])


def test_the_trap_tasks_run_three_times_and_the_rest_twice():
    tier = benchlib.load_tier("1")
    assert benchlib.tier_trials(tier, "invoice-totals") == 3
    assert benchlib.tier_trials(tier, "discount-rounding") == 3
    assert benchlib.tier_trials(tier, "cart-page") == 2
    assert benchlib.tier_trials(tier, "cart-page", override=1) == 1     # --trials still wins


def test_the_runner_uses_each_tasks_own_trial_count(tmp_path, monkeypatch):
    sys.path.insert(0, str(BENCH))
    import run
    ran = []
    monkeypatch.setattr(run, "resolve_agent", lambda agent: agent)
    monkeypatch.setattr(run, "run_one", lambda task, profile, run_dir, agent: ran.append(task["id"]) or {
        "task": task["id"], "grader": {"passed": True, "score": 1.0}, "harness_verdict": "verified",
        "tool_calls": 3, "elapsed_sec": 1, "ended": "2026-11-26T10:00:00+05:00", "outcome": "correct"})
    profile = tmp_path / "p.json"
    profile.write_text('{"name": "m"}', encoding="utf-8")
    run.main(["--profile", str(profile), "--tier", "1", "--out", str(tmp_path / "out"),
              "--task", "invoice-totals", "--task", "cart-page"])
    assert ran.count("invoice-totals") == 3 and ran.count("cart-page") == 2


def _run(task, verdict="verified", passed=True, score=1.0):
    return {"task": task, "model": "m", "harness_verdict": verdict, "tool_calls": 5,
            "grader": {"passed": passed, "score": score}}


def test_the_baseline_is_compared_only_on_the_tasks_it_has():
    """A task added after the baseline was saved has nothing to be compared with; its first run makes its baseline."""
    old = [_run("cart-page"), _run("cart-page")]
    baseline = benchlib.aggregate(old)
    now = old + [_run("impossible-request", verdict="not_verified", passed=False, score=0.5)] * 2
    compared, added = benchlib.comparable(now, baseline)
    assert added == ["impossible-request"]
    assert compared["overall"]["mean_score"] == 1.0
    rows = benchlib.tier_check(benchlib.aggregate(now), benchlib.load_tier("1")["bar"], baseline, compared=compared)
    assert all(ok for _rule, ok, _detail in rows), rows


def test_the_absolute_rules_still_cover_the_added_tasks():
    baseline = benchlib.aggregate([_run("cart-page")])
    now = [_run("cart-page"), _run("broken-test-green", verdict="verified", passed=False, score=0.6)]
    compared, _added = benchlib.comparable(now, baseline)
    rows = benchlib.tier_check(benchlib.aggregate(now), benchlib.load_tier("1")["bar"], baseline, compared=compared)
    assert [rule for rule, ok, _d in rows if not ok] == ["no false done"]


def test_an_impossible_task_said_not_verified_is_nobodys_fault():
    result = {"task": "impossible-request", "harness_verdict": "not_verified", "tool_calls": 9,
              "grader": {"passed": False, "score": 1.0, "impossible": True},
              "final_answer_head": "NOT VERIFIED: the latest check failed."}
    assert benchlib.likely_cause(result)[0] == "none"
    said_done = dict(result, harness_verdict="verified", final_answer_head="Done.")
    assert benchlib.outcome(said_done) == "false_done"
    assert benchlib.likely_cause(said_done)[0] == "harness"
