"""generate_tests must put back code its sub-agent was not allowed to touch.

The fingerprint check already noticed the overwrite and said so, but detecting
it is not enough: by then the write has landed, and a sub-agent that replaced a
source file cannot be assumed to have written a correct revision of it. A live
audit hit exactly this — the file under test came back as an older revision with
several functions missing. Snapshotting the bytes first turns the warning into a
repair.
"""
import json

import pytest


@pytest.fixture
def ready(engine, tmp_path):
    engine.PERMISSION_MODE = "full-auto"
    engine.ALLOWED_PATHS = [tmp_path]
    return engine


SOURCE = "def starting_rung():\n    return 1\n\n\ndef select(x):\n    return x\n"


def _run_autotest(engine, source, subagent):
    """Drive _exec_autotest with a stubbed sub-agent, restoring the real one."""
    real = engine._exec_subagent
    engine._exec_subagent = subagent
    try:
        return engine._exec_autotest({"filename": str(source)})
    finally:
        engine._exec_subagent = real


def test_a_clobbered_source_file_is_restored(ready, tmp_path):
    source = tmp_path / "undertest.py"
    source.write_text(SOURCE, encoding="utf-8")

    def clobbering(args, depth=0):
        source.write_text("def old_revision():\n    pass\n", encoding="utf-8")
        return "[subagent:test-writer] wrote 12 tests, all passing"

    report = _run_autotest(ready, source, clobbering)

    assert source.read_text(encoding="utf-8") == SOURCE
    assert "starting_rung" in source.read_text(encoding="utf-8")
    assert "MODIFIED" in report


def test_the_report_says_the_file_was_put_back(ready, tmp_path):
    source = tmp_path / "undertest.py"
    source.write_text(SOURCE, encoding="utf-8")

    def clobbering(args, depth=0):
        source.write_text("weakened\n", encoding="utf-8")
        return "all passing"

    report = _run_autotest(ready, source, clobbering)

    assert "restored" in report.lower()


def test_the_subagents_own_report_is_still_returned(ready, tmp_path):
    """The warning is prepended, not substituted — the report still matters."""
    source = tmp_path / "undertest.py"
    source.write_text(SOURCE, encoding="utf-8")

    def clobbering(args, depth=0):
        source.write_text("weakened\n", encoding="utf-8")
        return "[subagent:test-writer] wrote tests/test_undertest.py, 12 tests"

    report = _run_autotest(ready, source, clobbering)

    assert "tests/test_undertest.py" in report


def test_a_deleted_source_file_is_restored(ready, tmp_path):
    source = tmp_path / "undertest.py"
    source.write_text(SOURCE, encoding="utf-8")

    def deleting(args, depth=0):
        source.unlink()
        return "done"

    report = _run_autotest(ready, source, deleting)

    assert source.exists()
    assert source.read_text(encoding="utf-8") == SOURCE
    assert "MODIFIED" in report


# --- a well-behaved run must be left completely alone ----------------------
def test_an_untouched_source_produces_no_warning(ready, tmp_path):
    source = tmp_path / "undertest.py"
    source.write_text(SOURCE, encoding="utf-8")

    def polite(args, depth=0):
        return "[subagent:test-writer] wrote 5 tests, all passing"

    report = _run_autotest(ready, source, polite)

    assert report == "[subagent:test-writer] wrote 5 tests, all passing"
    assert source.read_text(encoding="utf-8") == SOURCE


def test_a_test_file_written_alongside_is_not_disturbed(ready, tmp_path):
    """Only the file under test is protected; the sub-agent's real output stays."""
    source = tmp_path / "undertest.py"
    source.write_text(SOURCE, encoding="utf-8")
    written_test = tmp_path / "test_undertest.py"

    def writes_a_test(args, depth=0):
        written_test.write_text("def test_ok():\n    assert True\n", encoding="utf-8")
        source.write_text("clobbered\n", encoding="utf-8")
        return "done"

    _run_autotest(ready, source, writes_a_test)

    assert source.read_text(encoding="utf-8") == SOURCE
    assert written_test.exists(), "the sub-agent's test file must survive"


def test_the_guard_declines_rather_than_holding_a_huge_file_in_memory(ready, tmp_path):
    """Bounded like plan_audit_revert: past the cap it warns but does not snapshot."""
    source = tmp_path / "huge.py"
    source.write_text("# " + "x" * 4096 + "\n", encoding="utf-8")
    ready.PLAN_REVERT_MAX_BYTES = 128

    def clobbering(args, depth=0):
        source.write_text("clobbered\n", encoding="utf-8")
        return "done"

    report = _run_autotest(ready, source, clobbering)

    assert "MODIFIED" in report
    assert "restored" not in report.lower()
