"""Gateway adapters whose dependency is missing are reported, not just logged.

capabilities.GATEWAY names each disabled adapter (for /doctor and /status),
and the gateway prints one startup line listing them.
"""
import builtins


from agent8088 import capabilities as C
from agent8088.gateway import dependencies as D


def _spec(platform):
    return next(s for s in D.ADAPTERS if s.platform == platform)


def test_no_enabled_adapter_reports_nothing():
    C.report(C.GATEWAY, active="", state=C.DEGRADED)
    assert D.report({}) == []
    assert C.get(C.GATEWAY) is None


def test_doctor_path_finds_missing_dependencies_without_importing(monkeypatch):
    monkeypatch.setattr(D, "dependency_missing", lambda spec: spec.platform == "slack")
    disabled = D.report({"slack_enabled": "1", "email_enabled": "true"})
    assert [spec.platform for spec, _ in disabled] == ["slack"]
    entry = C.get(C.GATEWAY)
    assert entry.state == C.DEGRADED
    assert entry.active == "email"
    assert "slack: slack-bolt not installed" in entry.reason
    assert entry.impact == "slack adapter disabled"
    assert entry.fix == D.GATEWAY_FIX


def test_every_enabled_adapter_disabled_is_unavailable():
    disabled = [(_spec("discord"), "discord.py not installed")]
    D.report({"discord_enabled": "1"}, disabled)
    entry = C.get(C.GATEWAY)
    assert entry.state == C.UNAVAILABLE
    assert C.doctor_status(entry) == "warn"   # optional: never a /doctor failure


def test_all_adapters_loading_is_ok(monkeypatch):
    monkeypatch.setattr(D, "dependency_missing", lambda spec: False)
    assert D.report({"telegram_enabled": "1"}) == []
    assert C.get(C.GATEWAY).ok


def test_startup_notice_lists_disabled_adapters_and_the_fix():
    notice = D.startup_notice([(_spec("slack"), "slack-bolt not installed"),
                               (_spec("telegram"), "python-telegram-bot not installed")])
    assert notice.startswith("gateway: adapters disabled: ")
    assert "slack (slack-bolt not installed)" in notice
    assert "telegram (python-telegram-bot not installed)" in notice
    assert D.GATEWAY_FIX in notice
    assert D.startup_notice([]) == ""


def test_build_runner_records_and_reports_an_adapter_that_fails_to_import(monkeypatch):
    from agent8088.gateway import runner as R
    monkeypatch.setattr(R.A, "APP_CONFIG", {"slack_enabled": "1"})
    monkeypatch.setattr(R.A, "PERMISSION_MODE", R.A.PERMISSION_MODE)
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("slack_bolt"):
            raise ImportError("No module named 'slack_bolt'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.delitem(__import__("sys").modules, "agent8088.gateway.platforms.slack", raising=False)
    monkeypatch.setattr(D, "dependency_missing", lambda spec: True)
    runner = R.build_runner()
    assert runner.adapters == []
    assert [(spec.platform, why) for spec, why in runner.disabled_adapters] == [
        ("slack", "slack-bolt not installed")]
    assert C.get(C.GATEWAY).state == C.UNAVAILABLE


def test_doctor_lists_a_disabled_gateway_adapter(monkeypatch):
    from agent8088 import cli
    monkeypatch.setattr(cli.A, "APP_CONFIG", {**cli.A.APP_CONFIG, "slack_enabled": "1"})
    monkeypatch.setattr(D, "dependency_missing", lambda spec: spec.platform == "slack")
    cli._doctor_refresh_capabilities()
    rows = [c for c in cli._doctor_capability_checks() if c["name"] == "Limited: gateway"]
    assert len(rows) == 1
    assert rows[0]["status"] == "warn"
    assert "slack adapter disabled" in rows[0]["detail"]
    assert rows[0]["fix"] == D.GATEWAY_FIX
