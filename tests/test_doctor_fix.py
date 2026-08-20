"""doctor --fix's reinstall helper, exercised against a fake pip/uv rather than a
real broken package -- reinstalling a genuinely broken native wheel isn't something
a test should attempt to reproduce; this pins the subprocess/fallback logic instead.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent8088 import cli  # noqa: E402


def test_reinstall_package_succeeds_via_pip(monkeypatch):
    def fake_run(cmd, **kwargs):
        assert cmd[:3] == [sys.executable, "-m", "pip"]
        return subprocess.CompletedProcess(cmd, 0, stdout="Successfully installed ddgs", stderr="")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    ok, detail = cli._reinstall_package("ddgs")
    assert ok is True
    assert "pip" in detail


def test_reinstall_package_falls_back_to_uv_when_pip_module_missing(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if "pip" in cmd and cmd[0] == sys.executable:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="No module named pip")
        return subprocess.CompletedProcess(cmd, 0, stdout="installed", stderr="")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/local/bin/uv" if name == "uv" else None)
    ok, detail = cli._reinstall_package("ddgs")
    assert ok is True
    assert "uv" in detail
    assert any(c[0] == "/usr/local/bin/uv" for c in calls)


def test_reinstall_package_reports_failure_when_both_fail(monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="permission denied")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    ok, detail = cli._reinstall_package("ddgs")
    assert ok is False
    assert "permission denied" in detail


class _RecordingConsole:
    """Stand-in for cli.console that records every print() call as text,
    so assertions don't depend on rich's stdout-capture behavior."""

    def __init__(self):
        self.lines = []

    def print(self, *args, **kwargs):
        self.lines.append(" ".join(str(a) for a in args))

    def text(self):
        return "\n".join(self.lines)


def _stub_doctor_network(monkeypatch):
    # cmd_doctor's table-building path also hits _endpoint_probe (real socket
    # connect) and A.sandbox_status() (reads real host state) -- stub both so
    # these tests never touch the network or the host's real config, same as
    # test_dump_redaction.py does for cmd_dump.
    monkeypatch.setattr(cli, "_endpoint_probe", lambda endpoint: "stubbed")
    monkeypatch.setattr(cli.A, "sandbox_status", lambda: {
        "requested": "auto", "resolved": "unavailable",
        "detail": "test", "verification": "n/a",
    })


def test_doctor_fix_reinstalls_ddgs_when_broken_and_reports_success(monkeypatch):
    """cmd_doctor's own `if fix:` block (not just _reinstall_package in
    isolation): ddgs starts broken, --fix should call _reinstall_package,
    then re-check ddgs and report the fix as successful."""
    _stub_doctor_network(monkeypatch)
    recorder = _RecordingConsole()
    monkeypatch.setattr(cli, "console", recorder)

    calls = {"n": 0}

    def fake_ddgs_installed():
        calls["n"] += 1
        # 1st call: the table's "Web search" row (broken).
        # 2nd call: the --fix block's own check (still broken -> triggers repair).
        # 3rd call: the post-repair re-check (now fixed).
        return calls["n"] >= 3

    monkeypatch.setattr(cli.A.web_search, "_ddgs_installed", fake_ddgs_installed)

    reinstall_calls = []

    def fake_reinstall(package):
        reinstall_calls.append(package)
        return True, "reinstalled ddgs via pip"

    monkeypatch.setattr(cli, "_reinstall_package", fake_reinstall)

    cli.cmd_doctor("--fix")

    assert reinstall_calls == ["ddgs"]
    assert "Fixed" in recorder.text()


def test_doctor_fix_is_a_no_op_when_ddgs_already_installed(monkeypatch):
    _stub_doctor_network(monkeypatch)
    recorder = _RecordingConsole()
    monkeypatch.setattr(cli, "console", recorder)
    monkeypatch.setattr(cli.A.web_search, "_ddgs_installed", lambda: True)

    reinstall_calls = []
    monkeypatch.setattr(
        cli, "_reinstall_package",
        lambda package: reinstall_calls.append(package) or (True, "unused"),
    )

    cli.cmd_doctor("--fix")

    assert reinstall_calls == []
    assert "No auto-repairable issues found" in recorder.text()


def test_doctor_reports_unknown_option_instead_of_silently_proceeding(monkeypatch):
    recorder = _RecordingConsole()
    monkeypatch.setattr(cli, "console", recorder)

    cli.cmd_doctor("--verbose")

    assert "unknown option" in recorder.text()
    # An unrecognized flag must not fall through into the real diagnostic
    # table (which would call _endpoint_probe / sandbox_status for real).
    assert "Doctor" not in recorder.text()
