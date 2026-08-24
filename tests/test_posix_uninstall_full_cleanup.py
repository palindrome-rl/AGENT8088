"""Coverage for the full-uninstall-cleanup fixes: agent8088's installer leaves
side effects outside $AGENT8088_HOME (a shell rc PATH line, crontab entries,
trace logs, a WhatsApp session dir, a shared Playwright browser cache) that
`--uninstall` didn't used to touch. Flag design (--workspace/--all/--yes/
--non-interactive/--dry-run) mirrors OpenClaw's `uninstall` command: program
files and installation side effects are always removed, user-generated data
is opt-in.
"""
import json
import sys
from pathlib import Path
from unittest import mock

import pytest

from agent8088 import cli

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX uninstall path only")


# --- CLI flag wiring (main() -> _run_uninstall), OpenClaw-style flags ------

def test_main_wires_workspace_and_all_and_yes_and_dry_run_into_run_uninstall(monkeypatch):
    captured = {}
    monkeypatch.setattr(cli, "_run_uninstall", lambda **kw: captured.update(kw) or True)
    monkeypatch.setattr(sys, "argv", ["agent8088", "--uninstall", "--all", "--yes", "--dry-run"])

    cli.main()

    assert captured == {"workspace": True, "assume_yes": True, "dry_run": True}


def test_main_workspace_flag_alone_sets_workspace_true(monkeypatch):
    captured = {}
    monkeypatch.setattr(cli, "_run_uninstall", lambda **kw: captured.update(kw) or True)
    monkeypatch.setattr(sys, "argv", ["agent8088", "--uninstall", "--workspace"])

    cli.main()

    assert captured["workspace"] is True


def test_main_bare_uninstall_defaults_everything_off(monkeypatch):
    captured = {}
    monkeypatch.setattr(cli, "_run_uninstall", lambda **kw: captured.update(kw) or True)
    monkeypatch.setattr(sys, "argv", ["agent8088", "--uninstall"])

    cli.main()

    assert captured == {"workspace": False, "assume_yes": False, "dry_run": False}


def test_main_non_interactive_without_yes_errors_without_running_uninstall(monkeypatch, capsys):
    called = []
    monkeypatch.setattr(cli, "_run_uninstall", lambda **kw: called.append(kw) or True)
    monkeypatch.setattr(sys, "argv", ["agent8088", "--uninstall", "--non-interactive"])

    cli.main()

    assert not called, "_run_uninstall must not run when --non-interactive lacks --yes"
    assert "requires --yes" in capsys.readouterr().out


def test_main_non_interactive_with_yes_proceeds(monkeypatch):
    captured = {}
    monkeypatch.setattr(cli, "_run_uninstall", lambda **kw: captured.update(kw) or True)
    monkeypatch.setattr(sys, "argv", ["agent8088", "--uninstall", "--non-interactive", "--yes"])

    cli.main()

    assert captured["assume_yes"] is True


# --- _remove_agent8088_path_exports -----------------------------------------

def test_remove_agent8088_path_exports_strips_exact_line(tmp_path, monkeypatch):
    link_dir = tmp_path / ".local" / "bin"
    rc = tmp_path / ".zshrc"
    rc.write_text(
        'export SOME_VAR=1\n'
        f'export PATH="{link_dir}:$PATH"\n'
        'export ANOTHER=2\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(cli, "_agent8088_link_dir", lambda: link_dir)

    removed = cli._remove_agent8088_path_exports()

    assert removed == 1
    kept = rc.read_text(encoding="utf-8")
    assert "SOME_VAR" in kept and "ANOTHER" in kept
    assert str(link_dir) not in kept


def test_remove_agent8088_path_exports_leaves_hand_edited_lines(tmp_path, monkeypatch):
    link_dir = tmp_path / ".local" / "bin"
    rc = tmp_path / ".zshrc"
    # A user who customized the line by hand (different order/comment) is not
    # touched - matching only the exact installer-written line is the safe
    # default (a substring match risks deleting an unrelated hand-written line).
    rc.write_text(f'export PATH="$PATH:{link_dir}"  # my custom order\n', encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(cli, "_agent8088_link_dir", lambda: link_dir)

    removed = cli._remove_agent8088_path_exports()

    assert removed == 0
    assert str(link_dir) in rc.read_text(encoding="utf-8")


# --- _remove_agent8088_crontab_entries --------------------------------------

def test_remove_agent8088_crontab_entries_filters_by_marker(monkeypatch):
    existing = "0 9 * * * /usr/bin/backup.sh\n* * * * * agent8088 --gateway # agent8088\n"
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:2] == ["crontab", "-l"]:
            return mock.Mock(returncode=0, stdout=existing, stderr="")
        if cmd[:2] == ["crontab", "-"]:
            calls.append(("stdin", kwargs.get("input")))
            return mock.Mock(returncode=0, stdout="", stderr="")
        raise AssertionError(f"unexpected command {cmd}")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    removed = cli._remove_agent8088_crontab_entries()

    assert removed == 1
    written = calls[-1][1]
    assert "backup.sh" in written
    assert "# agent8088" not in written


def test_remove_agent8088_crontab_entries_noop_when_no_crontab(monkeypatch):
    def fake_run(cmd, **kwargs):
        return mock.Mock(returncode=1, stdout="", stderr="no crontab for user")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    removed = cli._remove_agent8088_crontab_entries()  # must not raise
    assert removed == 0


# --- _remove_agent8088_workspace_data ---------------------------------------

def test_remove_agent8088_workspace_data_removes_default_dirs(tmp_path, monkeypatch):
    trace_dir = tmp_path / "Documents" / "agent8088" / "traces"
    trace_dir.mkdir(parents=True)
    (trace_dir / "trace.json").write_text("{}", encoding="utf-8")
    wa_dir = tmp_path / ".local" / "share" / "agent8088" / "whatsapp" / "session"
    wa_dir.mkdir(parents=True)

    monkeypatch.delenv("AGENT8088_TRACE_DIR", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    removed = cli._remove_agent8088_workspace_data()

    assert removed == 2
    assert not trace_dir.exists()
    assert not wa_dir.exists()
    # Empty ancestor directories left behind by removing the leaf dirs should
    # be pruned too - found via a real end-to-end run where they lingered as
    # harmless-but-untidy empty folders under ~/.local/share.
    assert not (tmp_path / ".local" / "share" / "agent8088").exists()
    assert not (tmp_path / "Documents" / "agent8088").exists()


def test_remove_agent8088_workspace_data_stops_pruning_at_a_non_empty_ancestor(tmp_path, monkeypatch):
    wa_dir = tmp_path / ".local" / "share" / "agent8088" / "whatsapp" / "session"
    wa_dir.mkdir(parents=True)
    # A sibling directory under the same parent that isn't ours - pruning must
    # stop here rather than deleting a directory something else put data in.
    (tmp_path / ".local" / "share" / "agent8088" / "not-ours").mkdir()
    monkeypatch.delenv("AGENT8088_TRACE_DIR", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    cli._remove_agent8088_workspace_data()

    assert not wa_dir.exists()
    assert (tmp_path / ".local" / "share" / "agent8088" / "not-ours").exists()
    assert (tmp_path / ".local" / "share" / "agent8088").exists()


def test_remove_agent8088_workspace_data_skips_customized_trace_dir(tmp_path, monkeypatch):
    custom = tmp_path / "elsewhere" / "traces"
    custom.mkdir(parents=True)
    monkeypatch.setenv("AGENT8088_TRACE_DIR", str(custom))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    removed = cli._remove_agent8088_workspace_data()

    assert removed == 0
    assert custom.exists()


# --- _warn_shared_playwright_cache ------------------------------------------

def test_warn_shared_playwright_cache_reports_when_present(tmp_path, monkeypatch, capsys):
    cache_dir = tmp_path / ".cache" / "ms-playwright"
    cache_dir.mkdir(parents=True)
    monkeypatch.setattr(cli, "_shared_playwright_cache_dir", lambda: cache_dir)

    cli._warn_shared_playwright_cache()

    out = capsys.readouterr().out
    assert str(cache_dir) in out
    assert cache_dir.exists()  # never deleted


def test_warn_shared_playwright_cache_silent_when_absent(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_shared_playwright_cache_dir", lambda: tmp_path / "nope")

    cli._warn_shared_playwright_cache()

    assert capsys.readouterr().out == ""


# --- _describe_agent8088_side_effects (preview for confirmation/--dry-run) --

def test_describe_agent8088_side_effects_lists_path_line_and_workspace_dirs(tmp_path, monkeypatch):
    link_dir = tmp_path / ".local" / "bin"
    rc = tmp_path / ".zshrc"
    rc.write_text(f'export PATH="{link_dir}:$PATH"\n', encoding="utf-8")
    trace_dir = tmp_path / "Documents" / "agent8088" / "traces"
    trace_dir.mkdir(parents=True)

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(cli, "_agent8088_link_dir", lambda: link_dir)
    monkeypatch.delenv("AGENT8088_TRACE_DIR", raising=False)
    monkeypatch.setattr(cli.subprocess, "run",
                         lambda cmd, **kw: mock.Mock(returncode=1, stdout="", stderr=""))

    without_workspace = cli._describe_agent8088_side_effects(tmp_path / "agent8088", include_workspace=False)
    with_workspace = cli._describe_agent8088_side_effects(tmp_path / "agent8088", include_workspace=True)

    assert any("PATH line" in line and str(rc) in line for line in without_workspace)
    assert not any(str(trace_dir) in line for line in without_workspace)
    assert any(str(trace_dir) in line for line in with_workspace)


# --- --dry-run / --workspace / --yes wiring through _run_uninstall ---------

def test_run_uninstall_dry_run_deletes_nothing(tmp_path, monkeypatch, capsys):
    home = tmp_path / "agent8088"
    home.mkdir()
    (home / "config.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(cli, "_agent8088_home", lambda: home)
    monkeypatch.setattr(cli, "_describe_agent8088_side_effects", lambda *_a, **_kw: ["PATH line in ~/.zshrc"])

    result = cli._run_uninstall(dry_run=True)

    assert result is True
    assert home.exists()
    assert (home / "config.txt").exists()
    out = capsys.readouterr().out
    assert "PATH line in ~/.zshrc" in out
    assert "nothing was removed" in out


def test_run_uninstall_assume_yes_skips_prompt(tmp_path, monkeypatch):
    home = tmp_path / "agent8088"
    home.mkdir()
    monkeypatch.setattr(cli, "_agent8088_home", lambda: home)
    monkeypatch.setattr(cli, "_describe_agent8088_side_effects", lambda *_a, **_kw: [])
    monkeypatch.setattr(cli, "_remove_agent8088_shim", lambda _home: False)
    monkeypatch.setattr(cli, "_remove_agent8088_config_exports", lambda: 0)
    monkeypatch.setattr(cli, "_remove_agent8088_path_exports", lambda: 0)
    monkeypatch.setattr(cli, "_remove_agent8088_crontab_entries", lambda: 0)
    monkeypatch.setattr(cli, "_warn_shared_playwright_cache", lambda: None)

    def _fail_if_called(*_a, **_kw):
        raise AssertionError("input() must not be called when assume_yes=True")

    monkeypatch.setattr("builtins.input", _fail_if_called)

    result = cli._run_uninstall(assume_yes=True)

    assert result is True
    assert not home.exists()


def test_run_uninstall_workspace_flag_removes_data_default_keeps_it(tmp_path, monkeypatch):
    trace_dir = tmp_path / "Documents" / "agent8088" / "traces"
    trace_dir.mkdir(parents=True)
    monkeypatch.delenv("AGENT8088_TRACE_DIR", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    home = tmp_path / "agent8088-install"
    home.mkdir()
    monkeypatch.setattr(cli, "_agent8088_home", lambda: home)
    monkeypatch.setattr(cli, "_describe_agent8088_side_effects", lambda *_a, **_kw: [])
    monkeypatch.setattr(cli, "_remove_agent8088_shim", lambda _home: False)
    monkeypatch.setattr(cli, "_remove_agent8088_config_exports", lambda: 0)
    monkeypatch.setattr(cli, "_remove_agent8088_path_exports", lambda: 0)
    monkeypatch.setattr(cli, "_remove_agent8088_crontab_entries", lambda: 0)
    monkeypatch.setattr(cli, "_warn_shared_playwright_cache", lambda: None)
    monkeypatch.setattr("builtins.input", lambda *_a: "yes")

    result = cli._run_uninstall(workspace=False)

    assert result is True
    assert trace_dir.exists(), "default --uninstall (no --workspace) must keep user data"


def test_run_uninstall_workspace_flag_true_removes_default_data(tmp_path, monkeypatch):
    trace_dir = tmp_path / "Documents" / "agent8088" / "traces"
    trace_dir.mkdir(parents=True)
    monkeypatch.delenv("AGENT8088_TRACE_DIR", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    home = tmp_path / "agent8088-install"
    home.mkdir()
    monkeypatch.setattr(cli, "_agent8088_home", lambda: home)
    monkeypatch.setattr(cli, "_describe_agent8088_side_effects", lambda *_a, **_kw: [])
    monkeypatch.setattr(cli, "_remove_agent8088_shim", lambda _home: False)
    monkeypatch.setattr(cli, "_remove_agent8088_config_exports", lambda: 0)
    monkeypatch.setattr(cli, "_remove_agent8088_path_exports", lambda: 0)
    monkeypatch.setattr(cli, "_remove_agent8088_crontab_entries", lambda: 0)
    monkeypatch.setattr(cli, "_warn_shared_playwright_cache", lambda: None)
    monkeypatch.setattr("builtins.input", lambda *_a: "yes")

    result = cli._run_uninstall(workspace=True)

    assert result is True
    assert not trace_dir.exists(), "--workspace must remove default user data"
