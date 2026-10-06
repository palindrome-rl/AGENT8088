"""agent8088.install_state: installer-skipped stages -> capabilities.INSTALL.

The installers write $AGENT8088_HOME/install-state.json (see
test_installer_install_state.py); this is the reading side. Everything lives
under tmp_path; nothing reads the real home.
"""
import json
from pathlib import Path

import pytest

from agent8088 import capabilities as C
from agent8088 import install_state as S


def _write(tmp_path: Path, skipped, **extra) -> Path:
    file = tmp_path / "install-state.json"
    data = {"version": 1, "installed_at": "2026-10-06T12:00:00Z", "skipped": skipped}
    data.update(extra)
    file.write_text(json.dumps(data), encoding="utf-8")
    return file


@pytest.fixture
def nothing_satisfied(monkeypatch):
    """No stage counts as fixed since the install, whatever this machine has."""
    monkeypatch.setattr(S, "_STAGES", tuple((p, cap, (lambda: False) if check else None)
                                            for p, cap, check in S._STAGES))


def test_missing_file_reports_nothing_and_clears_a_stale_entry(tmp_path):
    C.report(C.INSTALL, active="partial", state=C.DEGRADED, reason="old")
    assert S.report(tmp_path / "absent.json") == []
    assert C.get(C.INSTALL) is None


@pytest.mark.parametrize("content", ["not json", "[]", '{"version": 2, "skipped": []}',
                                     '{"version": 1, "skipped": "x"}'])
def test_unreadable_or_foreign_file_is_ignored(tmp_path, content):
    file = tmp_path / "install-state.json"
    file.write_text(content, encoding="utf-8")
    assert S.report(file) == []
    assert C.get(C.INSTALL) is None


def test_skipped_stages_report_install_degraded_with_fixes(tmp_path, nothing_satisfied):
    file = _write(tmp_path, [
        {"stage": "Web UI build", "reason": "failed (exit 1)", "fix": "cd web; npm run build"},
        {"stage": "First-run setup", "reason": "did not complete", "fix": "agent8088 --setup"},
    ])
    items = S.report(file)
    assert [i["stage"] for i in items] == ["Web UI build", "First-run setup"]
    entry = C.get(C.INSTALL)
    assert entry.state == C.DEGRADED
    assert "at install time" in entry.reason and "2026-10-06T12:00:00Z" in entry.reason
    assert "Web UI build (failed (exit 1))" in entry.reason
    assert "Web UI build" in entry.impact and "First-run setup" in entry.impact
    assert "cd web; npm run build" in entry.fix and "agent8088 --setup" in entry.fix
    # Shown by the banner and /doctor through the registry.
    assert "install=partial" in C.banner_line()


def test_empty_ledger_clears_the_entry(tmp_path):
    C.report(C.INSTALL, active="partial", state=C.DEGRADED)
    assert S.report(_write(tmp_path, [])) == []
    assert C.get(C.INSTALL) is None


def test_a_live_subsystem_entry_wins_over_the_install_record(tmp_path, nothing_satisfied):
    # The browser reported itself (ok or not): it knows the current state, so
    # the install-time record of Chromium failing is not repeated.
    C.report(C.BROWSER, active="chromium")
    file = _write(tmp_path, [
        {"stage": "Chromium browser", "reason": "timed out after 10m", "fix": "python -m playwright install chromium"},
        {"stage": "Web UI build", "reason": "failed (exit 1)", "fix": "npm run build"},
    ])
    assert [i["stage"] for i in S.report(file)] == ["Web UI build"]
    assert "Chromium" not in C.get(C.INSTALL).impact


def test_a_stage_satisfied_since_the_install_is_dropped(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "_STAGES", (("chromium", C.BROWSER, lambda: True),))
    file = _write(tmp_path, [{"stage": "Chromium browser", "reason": "failed", "fix": "x"}])
    assert S.report(file) == []
    assert C.get(C.INSTALL) is None


def test_chromium_presence_is_read_from_the_browsers_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
    assert S._chromium_present() is False
    (tmp_path / "chromium-1234").mkdir()
    assert S._chromium_present() is True


def test_a_broken_probe_keeps_the_stage(tmp_path, monkeypatch):
    def boom():
        raise RuntimeError("probe failed")
    monkeypatch.setattr(S, "_STAGES", (("mem0", C.MEMORY, boom),))
    file = _write(tmp_path, [{"stage": "mem0 memory backend", "reason": "failed", "fix": "pip"}])
    assert [i["stage"] for i in S.report(file)] == ["mem0 memory backend"]


def test_utf8_bom_from_windows_powershell_is_accepted(tmp_path, nothing_satisfied):
    file = tmp_path / "install-state.json"
    file.write_bytes(b"\xef\xbb\xbf" + json.dumps(
        {"version": 1, "skipped": [{"stage": "Web UI", "reason": "r", "fix": "f"}]}).encode())
    assert [i["stage"] for i in S.report(file)] == ["Web UI"]


def test_home_follows_agent8088_home(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT8088_HOME", str(tmp_path))
    assert S.home() == tmp_path
