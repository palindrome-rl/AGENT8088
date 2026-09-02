import json

from agent8088.gateway.auth import Allowlist, expand_whatsapp_aliases
from agent8088.gateway.platforms.discord import markdown_to_discord
from agent8088.gateway.platforms.slack import markdown_to_slack
from agent8088.gateway.platforms.telegram import markdown_to_telegram
from agent8088.gateway.platforms.whatsapp import markdown_to_whatsapp
from agent8088.gateway.session import SessionStore, build_session_key


def test_gateway_session_round_trip_handles_windows_unsafe_key(tmp_path):
    store = SessionStore(tmp_path)
    key = build_session_key("slack", "channel", "C:team", "thread:1")
    messages = [{"role": "user", "content": "hello"}]

    store.save(key, messages)

    assert store.load(key) == messages
    assert store.list_all() == [key]
    assert ":" not in next(tmp_path.glob("*.json")).name
    store.clear(key)
    assert store.load(key) == []


def test_gateway_sessions_honor_agent_home(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT8088_HOME", str(tmp_path))
    assert SessionStore().dir == tmp_path / "gateway-sessions"


def test_gateway_allowlist_keeps_platform_identities_scoped():
    allowlist = Allowlist.from_config({
        "slack_allowed_users": "U123",
        "discord_allowed_users": "D456",
    })

    assert allowlist.is_allowed("U123", "slack") is True
    assert allowlist.is_allowed("U123", "discord") is False
    assert allowlist.is_allowed("D456", "discord") is True


def test_whatsapp_alias_mapping_is_bounded_and_reversible(tmp_path):
    (tmp_path / "lid-mapping-123.json").write_text(
        json.dumps("456@s.whatsapp.net"), encoding="utf-8"
    )

    assert expand_whatsapp_aliases("+123@s.whatsapp.net", tmp_path) == {"123", "456"}


def test_gateway_markdown_renderers_preserve_meaning():
    source = "**bold** [link](https://example.com)"

    assert "bold" in markdown_to_slack(source)
    assert "bold" in markdown_to_discord(source)
    assert "bold" in markdown_to_telegram(source)
    assert "bold" in markdown_to_whatsapp(source)
