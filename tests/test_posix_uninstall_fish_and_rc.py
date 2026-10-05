"""--uninstall removes what install.sh now writes beyond the login shell's rc:
the fish conf.d snippet (write_fish_config) and PATH / AGENT8088_CONFIG lines
in another shell's files, ~/.bash_login included. HOME is a tmp dir.
"""
import sys
from pathlib import Path

import pytest

from agent8088 import cli

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX uninstall path only")


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AGENT8088_LINK_DIR", str(tmp_path / ".local" / "bin"))
    # Never the real crontab or Docker.
    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(cli, "_agent8088_searxng_container_exists", lambda: False)
    monkeypatch.setattr(cli, "_remove_agent8088_searxng_container", lambda: False)
    monkeypatch.setattr(cli, "_remove_agent8088_crontab_entries", lambda: 0)
    return tmp_path


def _fish(home, text):
    path = home / ".config" / "fish" / "conf.d" / "agent8088.fish"
    path.parent.mkdir(parents=True)
    path.write_text(text)
    return path


INSTALLER_FISH = (
    "# Added by the agent8088 installer\n"
    'if not contains -- "/x/.local/bin" $PATH\n'
    '    set -gx PATH "/x/.local/bin" $PATH\n'
    "end\n"
    'set -gx AGENT8088_CONFIG "/x/.agent8088/config.txt"\n'
)


def test_the_installers_fish_snippet_is_removed(home):
    path = _fish(home, INSTALLER_FISH)
    assert cli._remove_agent8088_fish_conf() is True
    assert not path.exists()


def test_a_hand_written_fish_file_of_the_same_name_is_kept(home):
    path = _fish(home, "set -gx PATH /mine $PATH\n")
    assert cli._remove_agent8088_fish_conf() is False
    assert path.exists()


def test_the_fish_snippet_is_listed_before_removal(home):
    _fish(home, INSTALLER_FISH)
    lines = cli._describe_agent8088_side_effects(home / ".agent8088")
    assert any("agent8088.fish" in line for line in lines)


def test_lines_in_bash_login_are_removed_too(home):
    link_dir = home / ".local" / "bin"
    rc = home / ".bash_login"
    rc.write_text(f'alias ll="ls -l"\nexport PATH="{link_dir}:$PATH"\n'
                  f'export AGENT8088_CONFIG="{home}/.agent8088/config.txt"\n')
    assert cli._remove_agent8088_path_exports() == 1
    assert cli._remove_agent8088_config_exports() == 1
    assert rc.read_text() == 'alias ll="ls -l"\n'


def test_uninstall_removes_the_fish_snippet(home, monkeypatch, capsys):
    path = _fish(home, INSTALLER_FISH)
    monkeypatch.setenv("AGENT8088_HOME", str(home / ".agent8088"))
    (home / ".agent8088").mkdir()
    monkeypatch.setattr(cli, "_warn_shared_playwright_cache", lambda: None)
    assert cli._run_uninstall(assume_yes=True) is True
    assert not path.exists()
    assert "agent8088.fish" in capsys.readouterr().out
