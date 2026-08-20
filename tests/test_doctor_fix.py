"""doctor --fix's reinstall helper, exercised against a fake pip/uv rather than a
real broken package -- reinstalling a genuinely broken native wheel isn't something
a test should attempt to reproduce; this pins the subprocess/fallback logic instead.
"""
import subprocess
import sys
from pathlib import Path

import pytest

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
