"""After repeated tool failures the model is shown what the environment looks
like, so a run of unrelated errors does not end in "the environment is
inaccessible" with nothing checked.

Seen live on development with glm-5.3-flash: with the working directory
missing, four different tools failed four different ways and the model
concluded "the workspace itself appears to be missing" without one look.
"""
import json


def _fail_then(engine, monkeypatch, results):
    outcomes = iter(results)
    monkeypatch.setattr(engine, "run_tool", lambda name, args, **_k: next(outcomes))


def _call(engine):
    return engine.exec_tool("read_text", json.dumps({"filename": "x"}))


def _layout(engine, monkeypatch, tmp_path):
    (tmp_path / "data.csv").write_text("a,1\n")
    (tmp_path / ".env").write_text("SECRET=1\n")
    monkeypatch.setattr(engine, "SHELL_CWD", tmp_path)
    monkeypatch.setattr(engine, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(engine, "ARTIFACTS_ROOT", tmp_path / "artifacts")
    engine.reset_turn_counters()


def test_third_consecutive_failure_carries_a_diagnostic(engine, monkeypatch, tmp_path):
    _layout(engine, monkeypatch, tmp_path)
    _fail_then(engine, monkeypatch, ["Error: one", "Error: two", "Error: three"])
    first, second, third = _call(engine), _call(engine), _call(engine)
    assert "Read-only check" not in first + second
    assert "3 tool calls in a row failed" in third
    assert f"Commands start in: {tmp_path}" in third
    assert "data.csv" in third
    assert ".env" not in third          # hidden names are not listed
    assert "free" in third              # disk space


def test_shown_once_per_turn_and_rearmed_next_turn(engine, monkeypatch, tmp_path):
    _layout(engine, monkeypatch, tmp_path)
    _fail_then(engine, monkeypatch, ["Error: x"] * 8)
    results = [_call(engine) for _ in range(5)]
    assert sum("Read-only check" in r for r in results) == 1
    engine.reset_turn_counters()
    results = [_call(engine) for _ in range(3)]
    assert "Read-only check" in results[-1]


def test_a_success_resets_the_streak(engine, monkeypatch, tmp_path):
    _layout(engine, monkeypatch, tmp_path)
    _fail_then(engine, monkeypatch, ["Error: a", "Error: b", "fine", "Error: c", "Error: d"])
    results = [_call(engine) for _ in range(5)]
    assert not any("Read-only check" in r for r in results)


def test_reports_a_missing_working_directory(engine, monkeypatch, tmp_path):
    _layout(engine, monkeypatch, tmp_path)
    missing = tmp_path / "app"
    monkeypatch.setattr(engine, "SHELL_CWD", missing)
    monkeypatch.setattr(engine, "LAUNCH_DIR", tmp_path / "gone")
    monkeypatch.setattr(engine, "PROJECT_ROOT", tmp_path / "gone-too")
    _fail_then(engine, monkeypatch, ["Error: x"] * 3)
    third = [_call(engine) for _ in range(3)][-1]
    assert f"the configured working directory {missing} does not exist" in third


def test_escalations_are_not_failures(engine, monkeypatch, tmp_path):
    _layout(engine, monkeypatch, tmp_path)
    escalation = "ESCALATION_REQUEST\x1f{}"
    _fail_then(engine, monkeypatch, ["Error: a", escalation, "Error: b", "Error: c"])
    results = [_call(engine) for _ in range(4)]
    assert not any("Read-only check" in r for r in results[:3])
