"""Turning verification on should not require editing a file and restarting.

`plan_audit=1` in config.txt is the kind of setting people want to try for one
task and then reconsider once they have seen what it costs. A slash command makes
that a decision you can take in the moment, and — because it writes through to the
config the same way the other preferences do — one you do not have to retake every
session.
"""
import io

from rich.console import Console

import agent8088.cli as cli


def _captured(monkeypatch):
    out = io.StringIO()
    monkeypatch.setattr(cli, "console", Console(file=out, width=140, color_system=None))
    return out


def _isolate_config(monkeypatch, tmp_path):
    """Never write to the developer's real config.txt."""
    path = tmp_path / "config.txt"
    path.write_text("allowed_paths=.\n", encoding="utf-8")
    monkeypatch.setattr(cli.A, "CONFIG_PATH", path)
    return path


def test_audit_on_turns_verification_on(monkeypatch, tmp_path):
    _captured(monkeypatch)
    _isolate_config(monkeypatch, tmp_path)
    monkeypatch.setattr(cli.A, "PLAN_AUDIT", False)

    cli.cmd_audit("on")

    assert cli.A.PLAN_AUDIT is True


def test_audit_off_turns_it_off(monkeypatch, tmp_path):
    _captured(monkeypatch)
    _isolate_config(monkeypatch, tmp_path)
    monkeypatch.setattr(cli.A, "PLAN_AUDIT", True)

    cli.cmd_audit("off")

    assert cli.A.PLAN_AUDIT is False


def test_bare_audit_reports_without_changing_anything(monkeypatch, tmp_path):
    out = _captured(monkeypatch)
    _isolate_config(monkeypatch, tmp_path)
    monkeypatch.setattr(cli.A, "PLAN_AUDIT", True)

    cli.cmd_audit("")

    assert cli.A.PLAN_AUDIT is True, "asking is not setting"
    assert "on" in out.getvalue()


def test_the_setting_survives_a_restart(monkeypatch, tmp_path):
    """Written through to config.txt, so it is not re-decided every session."""
    _captured(monkeypatch)
    path = _isolate_config(monkeypatch, tmp_path)
    monkeypatch.setattr(cli.A, "PLAN_AUDIT", False)

    cli.cmd_audit("on")

    assert "plan_audit=1" in path.read_text(encoding="utf-8")
    assert cli.A.APP_CONFIG["plan_audit"] == "1"


def test_turning_it_off_is_written_through_too(monkeypatch, tmp_path):
    _captured(monkeypatch)
    path = _isolate_config(monkeypatch, tmp_path)
    monkeypatch.setattr(cli.A, "PLAN_AUDIT", True)

    cli.cmd_audit("off")

    assert "plan_audit=0" in path.read_text(encoding="utf-8")


def test_turning_it_on_says_what_it_costs(monkeypatch, tmp_path):
    """An extra model call per mutating step is the whole reason it is off by
    default. Enabling it without saying so hands the user a surprise bill."""
    out = _captured(monkeypatch)
    _isolate_config(monkeypatch, tmp_path)
    monkeypatch.setattr(cli.A, "PLAN_AUDIT", False)

    cli.cmd_audit("on")

    assert "token" in out.getvalue().lower()


def test_status_reports_the_last_turns_verification_share(monkeypatch, tmp_path):
    out = _captured(monkeypatch)
    _isolate_config(monkeypatch, tmp_path)
    monkeypatch.setattr(cli.A, "PLAN_AUDIT", True)
    monkeypatch.setattr(cli.A, "last_audit_share", lambda: 0.14)

    cli.cmd_audit("status")

    assert "14%" in out.getvalue()


def test_an_unknown_argument_explains_itself_and_changes_nothing(monkeypatch, tmp_path):
    out = _captured(monkeypatch)
    _isolate_config(monkeypatch, tmp_path)
    monkeypatch.setattr(cli.A, "PLAN_AUDIT", False)

    cli.cmd_audit("maybe")

    assert cli.A.PLAN_AUDIT is False
    assert "/audit" in out.getvalue()


def test_audit_is_reachable_as_a_slash_command():
    assert cli.COMMANDS["audit"] is cli.cmd_audit
    assert "audit" in cli._COMPLETABLE_COMMANDS


def test_help_lists_audit(monkeypatch):
    out = _captured(monkeypatch)

    cli.cmd_help("")

    assert "/audit" in out.getvalue()


def test_a_config_write_failure_does_not_leave_a_lie_on_screen(monkeypatch, tmp_path):
    """If the setting could not be persisted, say so — reporting it as saved when
    it will be gone next launch is worse than not saving it."""
    out = _captured(monkeypatch)
    _isolate_config(monkeypatch, tmp_path)
    monkeypatch.setattr(cli.A, "PLAN_AUDIT", False)

    def boom(*a, **k):
        raise OSError("read-only file system")

    monkeypatch.setattr(cli.A, "update_simple_config", boom)

    cli.cmd_audit("on")

    assert cli.A.PLAN_AUDIT is True, "the session setting still applies"
    rendered = out.getvalue()
    assert "this session" in rendered or "not saved" in rendered
