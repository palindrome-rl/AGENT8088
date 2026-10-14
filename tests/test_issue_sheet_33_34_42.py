"""Issue-sheet rows S33, S34 and S42.

S33 the post-check cap must not claim success, and must not report a
     cap-stopped run as a clean completion.
S34 every generated .py is syntax-checked, not only test-shaped ones.
S42 install.ps1 records a web source stamp so a stale dist/ is rebuilt.
"""

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------- S33
def test_the_cap_nudge_does_not_claim_the_checks_passed():
    from agent8088 import engine
    assert engine.POST_CHECK_CAP_NUDGE != "Checks are complete. Give your final answer now."
    lowered = engine.POST_CHECK_CAP_NUDGE.lower()
    # The old sentence told a model its work was verified when it was not, and
    # the model believed it and stopped on a broken run.
    assert "report what's left" in lowered or "say plainly" in lowered


def test_a_cap_stopped_run_is_marked_incomplete():
    """A run the cap ended was never watched pass, so it cannot be clean."""
    from agent8088 import engine
    src = (ROOT / "src" / "agent8088" / "engine.py").read_text(encoding="utf-8")
    matches = re.findall(
        r'"incomplete": bool\((.*?)\)\}\)', src, re.S)
    assert matches, "no final_answer incomplete flag found"
    assert any("post_check_told" in m for m in matches), (
        "the cap-stopped flag is missing from the incomplete decision: %r" % matches)


def test_run_end_reason_reports_checks_failed_for_an_incomplete_answer():
    from agent8088 import cli
    steps = [{"type": "final_answer", "content": "done", "incomplete": True}]
    assert cli._run_end_reason(steps) == "checks_failed"
    steps = [{"type": "final_answer", "content": "done", "incomplete": False}]
    assert cli._run_end_reason(steps) != "checks_failed"


# --------------------------------------------------------------------- S34
def test_a_non_test_python_file_with_broken_syntax_is_refused():
    """The gate used to fire only for test-shaped .py files.

    helpers.py with leaked markup in it is just as unimportable as a broken
    test, and it is the shape the audit actually reported.
    """
    src = (ROOT / "src" / "agent8088" / "engine.py").read_text(encoding="utf-8")
    assert not re.search(
        r'suffix\.lower\(\) == "\.py" and _looks_like_a_test\(', src), (
        "the ast.parse gate is still limited to test-shaped files")
    assert '_python_syntax_error(content)' in src


def test_python_syntax_error_spots_leaked_markup():
    from agent8088 import engine
    assert not engine._python_syntax_error("def ok():\n    return 1\n")
    assert engine._python_syntax_error("</think>\ndef broken(:\n")
    assert engine._python_syntax_error("```python\ndef ok():\n    return 1\n```")


# --------------------------------------------------------------------- S42
def _installer():
    return (ROOT / "install.ps1").read_text(encoding="utf-8")


def test_the_windows_installer_records_a_web_source_stamp():
    src = _installer()
    assert "Set-WebSourceStamp" in src
    assert "Get-WebSourceStamp" in src
    assert "HEAD:web" in src, "the stamp must be the web/ tree hash"
    assert ".agent8088-build-stamp" in src, "install.sh uses this filename; match it"


def test_the_windows_installer_rebuilds_a_stale_bundle():
    src = _installer()
    # The already-built short-circuit must consult the stamp, or a dist/ built
    # from older sources is served after a pull.
    assert re.search(r"Test-WebUIDist -WebDir \$webDir -WebTree \(Get-WebSourceStamp\)", src)
    # A dist/ with no stamp cannot be shown to match these sources.
    assert "return $false" in src


def test_the_sh_installer_is_the_reference_and_still_has_its_stamp():
    sh = (ROOT / "install.sh").read_text(encoding="utf-8")
    assert ".agent8088-build-stamp" in sh
    assert "rev-parse HEAD:web" in sh