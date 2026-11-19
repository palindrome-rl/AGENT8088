import sys

from agent8088 import cli


def test_cwd_config_wins_in_venv_even_before_it_exists(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT8088_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / "venv"))

    resolved = cli._resolve_config_path()

    assert resolved == tmp_path / "config.txt"


def test_global_config_used_outside_venv_when_cwd_config_absent(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT8088_CONFIG", raising=False)
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "base_prefix", sys.prefix, raising=False)

    resolved = cli._resolve_config_path()

    assert resolved != tmp_path / "config.txt"


def test_existing_cwd_config_wins_outside_venv_too(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT8088_CONFIG", raising=False)
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "base_prefix", sys.prefix, raising=False)
    (tmp_path / "config.txt").write_text("", encoding="utf-8")

    resolved = cli._resolve_config_path()

    assert resolved == tmp_path / "config.txt"
