import json
from pathlib import Path

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


# --- Audit fixes (docs/gateway-audit.md, 2026-09-23 batch) ------------------

def test_gateway_session_keys_cannot_collide_across_the_separator():
    from agent8088.gateway.session import build_session_key as build

    # L9: chat_id="a", thread="b:c" must differ from chat_id="a:b", thread="c".
    assert build("slack", "channel", "a", "b:c") != build("slack", "channel", "a:b", "c")


def test_corrupt_session_file_is_set_aside_not_fatal(tmp_path):
    store = SessionStore(tmp_path)
    key = build_session_key("slack", "channel", "C1")
    store._path(key).write_text('{"version": 2, "mess', encoding="utf-8")

    # Bricks the chat with "[error: ...]" on every message until /new (L5).
    assert store.load(key) == []
    assert store.load_trajectory(key) == {}
    assert list(tmp_path.glob("*.corrupt"))


def test_new_mid_turn_blocks_the_in_flight_save(tmp_path):
    store = SessionStore(tmp_path)
    key = build_session_key("slack", "channel", "C1")
    gen = store.generation(key)
    store.save(key, [{"role": "user", "content": "before"}], generation=gen)

    store.clear(key)  # /new while the turn is in flight

    store.save(key, [{"role": "user", "content": "stale turn"}], generation=gen)
    assert store.load(key) == []
    # A save taken after the clear still lands.
    store.save(key, [{"role": "user", "content": "fresh"}])
    assert store.load(key) == [{"role": "user", "content": "fresh"}]


def test_email_from_extraction_survives_a_reversed_angle_bracket():
    from agent8088.gateway.platforms.email import _extract_email_address

    assert _extract_email_address("Name <a@b.com>") == "a@b.com"
    assert _extract_email_address("plain@b.com") == "plain@b.com"
    assert _extract_email_address("weird > oops <a@b.com>") == "a@b.com"
    # M11: no '>' after the '<' made index(">", start) raise and abort the batch.
    assert _extract_email_address("weird > oops <a@b.com") == "weird > oops <a@b.com"


def test_reply_dedup_spares_code_fences_and_blank_lines():
    from agent8088.gateway.runner import _dedup_reply

    # The glm-5.2 loop: one phrase repeated 3x collapses to one.
    loop = "I'll do it now. " * 3 + "done"
    assert _dedup_reply(loop) == "I'll do it now. done"
    # Three identical log lines inside a fence are content, not a loop.
    fenced = "```\nERROR x\nERROR x\nERROR x\n```"
    assert _dedup_reply(fenced) == fenced
    # Consecutive blank lines are intentional spacing.
    spaced = "para one\n\n\npara two"
    assert _dedup_reply(spaced) == spaced


def test_rate_limiter_prunes_idle_users():
    from agent8088.gateway.runner import _RateLimiter

    clock = {"t": 0.0}
    limiter = _RateLimiter(5, now=lambda: clock["t"])
    for i in range(600):
        limiter.allow(f"u{i}")

    clock["t"] += 61.0  # everyone idle past the window
    limiter.allow("fresh")

    # L3: idle users are dropped; an active one never is (that would reset
    # their window and bypass the limit).
    assert list(limiter._hits) == ["fresh"]


def test_whatsapp_session_dir_empty_config_falls_to_the_documented_default():
    from agent8088.gateway.platforms.whatsapp import WhatsAppAdapter

    adapter = WhatsAppAdapter({}, None)
    # M1: Path("") is Path(".") and truthy, so the default never fired and
    # creds.json landed in the repo tree.
    assert adapter.session_dir != Path(".")
    assert str(adapter.session_dir).endswith(
        str(Path("~/.local/share/agent8088/whatsapp/session").expanduser()).replace("\\", "/")[-40:]
    ) or adapter.session_dir == Path.home() / ".local" / "share" / "agent8088" / "whatsapp" / "session"


def test_gateway_mode_and_plan_are_per_chat_not_process_global(tmp_path):
    import asyncio

    from agent8088 import engine as A
    from agent8088.gateway.platforms.base import MessageEvent
    from agent8088.gateway.runner import GatewayRunner, _PendingPlanApproval

    A.PERMISSION_MODE = "readonly"

    class FakeAdapter:
        platform = "fake"

        def __init__(self):
            self.sent = []

        async def send_message(self, chat_id, text, **meta):
            self.sent.append(text)

        async def connect(self):
            pass

        async def disconnect(self):
            pass

        async def edit_message(self, chat_id, msg_id, text):
            pass

        async def on_message(self, event):
            pass

    adapter = FakeAdapter()
    runner = GatewayRunner(SessionStore(tmp_path), Allowlist(["*"]))
    runner.register_adapter(adapter)
    key = build_session_key("fake", "private", "c1")

    async def scenario():
        # H3: /mode used to flip the process-global PERMISSION_MODE.
        await runner._handle_slash(MessageEvent(
            platform="fake", chat_id="c1", chat_type="private",
            user_id="u1", text="/mode full-auto", attachments=[]), "/mode")
        assert runner._chat_modes[key] == "full-auto"
        assert A.PERMISSION_MODE == "readonly"  # engine global untouched
        await runner._handle_slash(MessageEvent(
            platform="fake", chat_id="c1", chat_type="private",
            user_id="u1", text="/plan", attachments=[]), "/plan")
        assert key in runner._plan_keys
        # L1: an /approve typo must be rejected, not read as full-auto.
        entry = _PendingPlanApproval("c1")
        runner._pending_plan_approvals[("fake", "c1")] = entry
        await runner._handle_slash(MessageEvent(
            platform="fake", chat_id="c1", chat_type="private",
            user_id="u1", text="/approve banana", attachments=[]), "/approve")
        assert entry.mode == ""
        assert not entry.event.is_set()

    asyncio.run(scenario())


def test_slack_and_telegram_chunk_the_raw_text(tmp_path):
    import asyncio

    from agent8088.gateway.platforms.slack import SlackAdapter
    from agent8088.gateway.platforms.telegram import TelegramAdapter

    async def scenario():
        tg = TelegramAdapter({}, None)
        tg._app = object()  # bypass the "no app" guard
        seen = []

        async def fake_tg_send(chat_id, text):
            seen.append(text)
            return 1

        tg._send = fake_tg_send
        # M5: send_message must chunk raw text; _send converts exactly once.
        await tg.send_message("1", "**hi**")
        assert seen == ["**hi**"]

        slack = SlackAdapter({}, None)
        got = []

        async def fake_slack_send(chat_id, text, thread_ts=None):
            got.append(text)

            class R:
                ok = True
                message_id = "1"

            return R()

        slack._send = fake_slack_send
        # M9: a long reply must chunk, not get rejected at 39000.
        long_text = "x" * 45000
        await slack.send_message("1", long_text)
        assert len(got) == 2
        assert "".join(got) == long_text

    asyncio.run(scenario())
