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
