"""Each sub-agent profile can carry its own wall clock, and a timeout returns partial work."""
from unittest.mock import patch

import pytest


def _write_profile(directory, name, extra=""):
    (directory / f"{name}.md").write_text(
        f"---\nname: {name}\ndescription: d\ntools: read_text\nmax_turns: 4\n{extra}---\nDo it.\n",
        encoding="utf-8")


def test_profile_max_seconds_is_read_from_frontmatter(engine, tmp_path):
    _write_profile(tmp_path, "slow", "max_seconds: 45\n")
    _write_profile(tmp_path, "plain")
    specs = engine.load_subagent_specs(tmp_path)
    assert specs["slow"]["max_seconds"] == 45
    assert specs["plain"]["max_seconds"] == 0


@pytest.mark.parametrize("bad", ["soon", "-5", "0", ""])
def test_unusable_max_seconds_means_use_the_default(engine, tmp_path, bad):
    _write_profile(tmp_path, "odd", f"max_seconds: {bad}\n")
    assert engine.load_subagent_specs(tmp_path)["odd"]["max_seconds"] == 0


def test_config_override_beats_the_frontmatter(engine, tmp_path, monkeypatch):
    _write_profile(tmp_path, "slow", "max_seconds: 45\n")
    monkeypatch.setitem(engine.APP_CONFIG, "subagent_max_seconds.slow", "200")
    assert engine.load_subagent_specs(tmp_path)["slow"]["max_seconds"] == 200


def test_the_bundled_test_writer_is_time_boxed(engine):
    assert engine.SUBAGENT_SPECS["test-writer"]["max_seconds"] == 90
    assert engine._subagent_ceiling("test-writer") == (90, "subagent_max_seconds.test-writer")


def test_profiles_without_max_seconds_keep_the_global_default(engine, monkeypatch):
    monkeypatch.setattr(engine, "SUBAGENT_TIMEOUT_SECONDS", 300)
    assert engine._subagent_ceiling("coder") == (300, "subagent_timeout_seconds")
    assert engine._subagent_ceiling("no-such-profile") == (300, "subagent_timeout_seconds")


def test_the_auditor_keeps_its_own_ceiling(engine, monkeypatch):
    monkeypatch.setattr(engine, "PLAN_AUDIT_TIMEOUT_SECONDS", 120)
    assert engine._subagent_ceiling("auditor") == (120, "plan_audit_timeout_seconds")


def test_the_child_budget_carries_the_profile_limit(engine, monkeypatch):
    parent = engine._TurnBudget()
    monkeypatch.setattr(engine, "_active_budget", parent)
    seen = {}
    monkeypatch.setattr(engine, "run_agent",
                        lambda *a, **k: seen.update(budget=k.get("budget")) or "done")
    engine._exec_subagent({"agent_type": "test-writer", "task": "write tests"})
    assert seen["budget"].max_seconds == 90
    assert seen["budget"].seconds_setting == "subagent_max_seconds.test-writer"


def test_a_short_turn_still_caps_the_profile_limit(engine, monkeypatch):
    parent = engine._TurnBudget(max_seconds=100)
    parent.started -= 70  # 30 seconds left
    monkeypatch.setattr(engine, "_active_budget", parent)
    seen = {}
    monkeypatch.setattr(engine, "run_agent",
                        lambda *a, **k: seen.update(budget=k.get("budget")) or "done")
    engine._exec_subagent({"agent_type": "test-writer", "task": "write tests"})
    assert 0 < seen["budget"].max_seconds <= 30


def _run_to_timeout(engine, monkeypatch, written=("tests/test_a.py",), answer=None):
    """A sub-run that writes files, then runs out of time."""
    parent = engine._TurnBudget()
    monkeypatch.setattr(engine, "_active_budget", parent)
    monkeypatch.setattr(engine, "_TURN_FILES_TOUCHED", ["already.py"])

    def fake_run(*a, **k):
        engine._TURN_FILES_TOUCHED.extend(written)
        budget = k["budget"]
        budget.started -= budget.max_seconds + 5  # the clock has run out
        return answer or ("Time budget exceeded: 95 seconds elapsed (limit 90s). "
                          "Partial result so far:\nFAILED test_a.py::test_x - assert 1 == 2")

    monkeypatch.setattr(engine, "run_agent", fake_run)
    return engine._exec_subagent({"agent_type": "test-writer", "task": "write tests"})


def test_a_timeout_reports_partial_work_not_a_failure(engine, monkeypatch):
    result = _run_to_timeout(engine, monkeypatch)
    assert result.startswith("[subagent:test-writer]")
    assert "90s time limit" in result and "partial work, not a failure" in result
    assert "tests/test_a.py" in result
    assert "already.py" not in result, "files the parent wrote earlier are not the sub-agent's"
    assert "FAILED test_a.py::test_x" in result, "the last output must still be there"


def test_a_timeout_with_no_files_says_so(engine, monkeypatch):
    result = _run_to_timeout(engine, monkeypatch, written=())
    assert "had not written any file" in result


def test_many_files_are_summarised(engine, monkeypatch):
    result = _run_to_timeout(engine, monkeypatch, written=[f"t{i}.py" for i in range(14)])
    assert "(+4 more)" in result


def test_a_run_that_finished_normally_is_not_relabelled(engine, monkeypatch):
    """The clock can be a hair over when the answer arrives; only the loop's own
    time-budget ending is a timeout."""
    parent = engine._TurnBudget()
    monkeypatch.setattr(engine, "_active_budget", parent)

    def fake_run(*a, **k):
        k["budget"].started -= k["budget"].max_seconds + 5
        return "Wrote 3 tests, all pass."

    monkeypatch.setattr(engine, "run_agent", fake_run)
    result = engine._exec_subagent({"agent_type": "test-writer", "task": "write tests"})
    assert result == "[subagent:test-writer] Wrote 3 tests, all pass."


def test_generate_tests_still_checks_the_code_under_test_after_a_timeout(engine, monkeypatch, tmp_path):
    source = tmp_path / "mod.py"
    source.write_text("def f():\n    return 1\n", encoding="utf-8")
    monkeypatch.setattr(engine, "resolve_write_path", lambda raw: source)

    def timed_out(args, depth=0, **kw):
        source.write_text("def f():\n    return 2\n", encoding="utf-8")
        return "[subagent:test-writer] Sub-agent 'test-writer' reached its 90s time limit..."

    monkeypatch.setattr(engine, "_exec_subagent", timed_out)
    result = engine._exec_autotest({"filename": str(source)})
    assert result.startswith("WARNING:") and "MODIFIED" in result
    assert source.read_text(encoding="utf-8") == "def f():\n    return 1\n"
