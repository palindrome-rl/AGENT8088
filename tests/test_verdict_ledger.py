"""A verdict, once read, is remembered outside the truncatable conversation.

Tool results get clamped and eventually compacted away; a ledger entry does
not. So "this suite passed" survives the turn that learned it, and the agent can
check the record instead of re-running a suite it already has an answer for.
"""
import pytest


@pytest.fixture
def shell(engine, monkeypatch):
    """execute_shell with a scripted result and a call counter."""
    engine.PERMISSION_MODE = "full-auto"
    calls = []

    def fake(command, timeout=25, image=""):
        calls.append(command)
        return fake.output

    fake.output = "40 passed in 0.02s"
    monkeypatch.setattr(engine, "_exec_shell_command", fake)
    monkeypatch.setattr(engine, "_exec_process",
                        lambda command, timeout=25, shell=False: fake(command))
    return engine, fake, calls


def _run(engine, command):
    return engine.run_tool("execute_shell", {"command": command})


def test_a_passing_run_is_recorded(shell):
    engine, _, _ = shell
    _run(engine, "pytest -q")
    entry = engine.known_verdict("pytest -q")
    assert entry.status == "passed"
    assert "40 passed" in entry.summary


def test_a_failing_run_is_recorded_as_failed(shell):
    engine, fake, _ = shell
    fake.output = "1 failed, 39 passed in 0.02s\nCommand exited with status 1."
    _run(engine, "pytest -q")
    assert engine.known_verdict("pytest -q").status == "failed"


def test_output_with_no_summary_records_nothing(shell):
    engine, fake, _ = shell
    fake.output = "hello world"
    _run(engine, "echo hello world")
    assert engine.known_verdict("echo hello world").status == "unknown"


def test_repeating_a_passing_command_is_served_from_the_ledger(shell):
    engine, _, calls = shell
    first = _run(engine, "pytest -q")
    second = _run(engine, "pytest -q")

    assert len(calls) == 1, "the second run must not re-execute the command"
    assert "passed" in second.lower()
    assert "40 passed" in second
    assert "verdict ledger" in second.lower()
    assert "40 passed" in first


def test_a_failing_verdict_is_never_reused(shell):
    engine, fake, calls = shell
    fake.output = "1 failed, 39 passed\nCommand exited with status 1."
    _run(engine, "pytest -q")
    _run(engine, "pytest -q")
    # The agent re-runs a failure because it is presumably fixing it.
    assert len(calls) == 2


def test_an_unknown_verdict_is_never_reused(shell):
    engine, fake, calls = shell
    fake.output = "some prose with no summary"
    _run(engine, "make build")
    _run(engine, "make build")
    assert len(calls) == 2


def test_a_write_invalidates_a_recorded_pass(shell, tmp_path):
    engine, _, calls = shell
    _run(engine, "pytest -q")
    engine.note_mutation("wrote src/calc.py")
    _run(engine, "pytest -q")
    assert len(calls) == 2, "code changed, so the old pass says nothing"


def test_the_ledger_survives_conversation_truncation(shell):
    engine, _, _ = shell
    _run(engine, "pytest -q")
    # Whatever the model was shown can be clamped to nothing; the record stands.
    assert engine.known_verdict("pytest -q").status == "passed"
    assert engine.verdict_ledger_summary()
    assert "pytest -q" in engine.verdict_ledger_summary()
