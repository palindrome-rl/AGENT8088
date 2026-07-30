import os
import sys
from pathlib import Path

import pytest

from agent8088 import cli


def _fake_install(tmp_path, monkeypatch):
    home = tmp_path / ".agent8088"
    install_dir = home / "agent8088"
    install_dir.mkdir(parents=True)
    (home / "config.txt").write_text("default_provider=ollama\n", encoding="utf-8")

    link_dir = tmp_path / "bin"
    link_dir.mkdir()
    shim = link_dir / ("agent8088.exe" if os.name == "nt" else "agent8088")
    shim.write_text(f'exec "{install_dir}/venv/bin/python" -m agent8088.cli "$@"\n', encoding="utf-8")

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AGENT8088_HOME", str(home))
    monkeypatch.setenv("AGENT8088_LINK_DIR", str(link_dir))
    monkeypatch.setenv("AGENT8088_CONFIG", str(home / "config.txt"))
    return home, shim


def test_agent8088_home_defaults_to_installer_home_on_posix(tmp_path, monkeypatch):
    if os.name == "nt":
        pytest.skip("POSIX default only")
    monkeypatch.delenv("AGENT8088_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))

    assert cli._agent8088_home() == tmp_path / ".agent8088"


def test_uninstall_cancel_keeps_install_dir(tmp_path, monkeypatch, capsys):
    home, shim = _fake_install(tmp_path, monkeypatch)
    monkeypatch.setattr("builtins.input", lambda prompt: "no")

    assert cli._run_uninstall() is False

    assert home.exists()
    assert shim.exists()
    assert "Uninstall cancelled" in capsys.readouterr().out


def test_uninstall_requires_exact_yes_and_removes_install_dir(tmp_path, monkeypatch, capsys):
    home, shim = _fake_install(tmp_path, monkeypatch)
    rc = tmp_path / ".zshrc"
    rc.write_text(
        'export AGENT8088_CONFIG="/tmp/old/config.txt"\nexport KEEP_ME=1\n',
        encoding="utf-8",
    )
    monkeypatch.setattr("builtins.input", lambda prompt: "yes")

    assert cli._run_uninstall() is True

    assert not home.exists()
    assert not shim.exists()
    assert "AGENT8088_CONFIG" not in os.environ
    assert rc.read_text(encoding="utf-8") == "export KEEP_ME=1\n"
    output = capsys.readouterr().out
    assert "Removed" in output
    assert "Done" in output


def test_single_dash_uninstall_flag_runs_uninstall(tmp_path, monkeypatch):
    home, _shim = _fake_install(tmp_path, monkeypatch)
    monkeypatch.setattr("builtins.input", lambda prompt: "yes")
    monkeypatch.setattr(sys, "argv", ["agent8088", "-uninstall"])

    cli.main()

    assert not home.exists()
