"""The installed command runs in its own venv. That is not "inside a project's venv", so it must find the user's config,
not ./config.txt, which is usually not there (and which silently meant the built-in Ollama defaults)."""
from pathlib import Path

import pytest

from agent8088 import cli, engine


@pytest.fixture
def homes(tmp_path, monkeypatch):
    local = tmp_path / "local"
    home = tmp_path / "home"
    (local / "agent8088").mkdir(parents=True)
    home.mkdir()
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.delenv("AGENT8088_HOME", raising=False)
    monkeypatch.delenv("AGENT8088_CONFIG", raising=False)
    return local / "agent8088", home / ".agent8088"


def test_the_installers_own_venv_is_recognised(homes):
    data_dir, dot_dir = homes
    assert engine._own_install_prefix(data_dir / "agent8088" / "venv")
    assert engine._own_install_prefix(dot_dir / "agent8088" / "venv")


def test_a_venv_elsewhere_is_not_the_install(homes, tmp_path):
    assert not engine._own_install_prefix(tmp_path / "myproject" / ".venv")


def test_agent8088_home_counts_as_an_install_folder(homes, tmp_path, monkeypatch):
    custom = tmp_path / "custom-home"
    monkeypatch.setenv("AGENT8088_HOME", str(custom))
    assert engine._own_install_prefix(custom / "venv")


def test_the_installed_command_is_not_a_project_venv(homes, monkeypatch):
    data_dir, _ = homes
    monkeypatch.setattr(engine.sys, "prefix", str(data_dir / "agent8088" / "venv"))
    monkeypatch.setattr(engine.sys, "base_prefix", str(data_dir / "python"))
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    assert engine._project_venv_active() is False


def test_a_project_venv_still_is(homes, tmp_path, monkeypatch):
    project = tmp_path / "myproject" / ".venv"
    monkeypatch.setattr(engine.sys, "prefix", str(project))
    monkeypatch.setattr(engine.sys, "base_prefix", str(tmp_path / "python"))
    monkeypatch.setenv("VIRTUAL_ENV", str(project))
    assert engine._project_venv_active() is True


def test_an_activated_venv_that_is_the_install_is_not_a_project(homes, monkeypatch):
    data_dir, _ = homes
    venv = data_dir / "agent8088" / "venv"
    monkeypatch.setattr(engine.sys, "prefix", str(venv))
    monkeypatch.setenv("VIRTUAL_ENV", str(venv))
    assert engine._project_venv_active() is False


def test_the_cli_resolves_the_global_config_for_the_installed_command(homes, tmp_path, monkeypatch):
    data_dir, _ = homes
    (data_dir / "config.txt").write_text("default_provider=openrouter\n", encoding="utf-8")
    elsewhere = tmp_path / "work"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr(engine.sys, "prefix", str(data_dir / "agent8088" / "venv"))
    monkeypatch.setattr(engine.sys, "base_prefix", str(data_dir / "python"))
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    assert cli._resolve_config_path() == data_dir / "config.txt"


def test_the_cli_still_uses_the_project_config_in_a_project_venv(homes, tmp_path, monkeypatch):
    project = tmp_path / "myproject"
    project.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setattr(engine.sys, "prefix", str(project / ".venv"))
    monkeypatch.setattr(engine.sys, "base_prefix", str(tmp_path / "python"))
    monkeypatch.setenv("VIRTUAL_ENV", str(project / ".venv"))
    assert cli._resolve_config_path() == project / "config.txt"


def test_an_explicit_config_variable_still_wins(homes, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT8088_CONFIG", str(tmp_path / "mine.txt"))
    assert cli._resolve_config_path() == tmp_path / "mine.txt"


def test_a_project_activation_is_honoured_even_when_the_launcher_uses_its_own_python(homes, tmp_path, monkeypatch):
    data_dir, _ = homes
    monkeypatch.setattr(engine.sys, "prefix", str(data_dir / "agent8088" / "venv"))
    monkeypatch.setattr(engine.sys, "base_prefix", str(data_dir / "python"))
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / "project" / ".venv"))
    monkeypatch.chdir(tmp_path)
    assert engine._project_venv_active() is True
    assert cli._resolve_config_path() == tmp_path / "config.txt"


def test_a_project_stored_under_the_data_directory_is_not_the_install(homes):
    data_dir, _ = homes
    assert not engine._own_install_prefix(data_dir / "projects" / "example" / ".venv")


def test_cli_writes_follow_the_same_existing_global_config_as_the_engine(homes, tmp_path, monkeypatch):
    data_dir, dot_dir = homes
    dot_dir.mkdir()
    (dot_dir / "config.txt").write_text("memory=0\n", encoding="utf-8")
    (data_dir / "config.txt").write_text("memory=1\n", encoding="utf-8")
    monkeypatch.setattr(engine.sys, "prefix", str(data_dir / "agent8088" / "venv"))
    monkeypatch.setattr(engine.sys, "base_prefix", str(data_dir / "python"))
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.chdir(tmp_path)
    assert cli._resolve_config_path() == dot_dir / "config.txt"
