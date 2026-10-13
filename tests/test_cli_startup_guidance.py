"""What the REPL tells a person before and during a turn when something is
off: the banner endpoint, startup notices, an unpulled Ollama model, the
first-run offer, offline model lists, missing keys, error answers, retry
notices, stream resets, and saving the session however the REPL ends.

No network: Ollama, endpoint probes and the model loop are mocked.
"""
import io
import sys

import pytest
from rich.console import Console

from agent8088 import cli


@pytest.fixture
def out(monkeypatch):
    buf = io.StringIO()
    monkeypatch.setattr(cli, "console", Console(file=buf, force_terminal=False, width=200))
    return buf


@pytest.fixture
def session(monkeypatch):
    fresh = cli.Session()
    monkeypatch.setattr(cli, "S", fresh)
    return fresh


# --- banner + startup notices -------------------------------------------------

def test_banner_shows_the_endpoint_requests_go_to(monkeypatch, out):
    monkeypatch.setattr(cli.A, "active_endpoint_url", lambda provider_name="": "http://real.box:9/v1")
    monkeypatch.setattr(cli.A, "sandbox_status", lambda: {"resolved": "local"})
    cli.banner()
    assert "http://real.box:9/v1" in out.getvalue()


def test_startup_notices_show_three_warnings_then_point_at_doctor(monkeypatch):
    monkeypatch.setattr(cli.A, "CONFIG_WARNINGS", [f"warning {i}" for i in range(5)])
    from agent8088 import memory
    monkeypatch.setattr(memory, "memory_status", lambda: {"engine": "native", "ok": True,
                                                          "error": "", "fix": ""})
    monkeypatch.setattr(cli, "_mcp_failure_notice", lambda: [])
    lines = cli._startup_notices()
    assert lines == ["warning 0", "warning 1", "warning 2", "2 more — /doctor"]


def test_startup_notices_include_degraded_memory(monkeypatch):
    monkeypatch.setattr(cli.A, "CONFIG_WARNINGS", [])
    from agent8088 import memory
    monkeypatch.setattr(memory, "memory_status", lambda: {"engine": "native", "ok": False,
                                                          "error": "database is locked",
                                                          "fix": "close the other agent8088"})
    monkeypatch.setattr(cli, "_mcp_failure_notice", lambda: [])
    assert cli._startup_notices() == ["Memory: database is locked — close the other agent8088"]


def test_mcp_failure_notice_waits_for_the_background_connect_then_speaks_once(monkeypatch):
    state = {"summary": {"pending": True, "connecting": [], "failed": [], "text": ""}}
    monkeypatch.setattr(cli.A, "mcp_status_summary", lambda: state["summary"])
    monkeypatch.setattr(cli, "_MCP_NOTICE", {"shown": False})
    assert cli._mcp_failure_notice() == []
    state["summary"] = {"pending": False, "connecting": [], "failed": ["db"],
                        "text": "MCP: 1 failed (see /mcp)"}
    assert cli._mcp_failure_notice() == ["MCP: 1 failed (see /mcp)"]
    assert cli._mcp_failure_notice() == []


def _ollama_active(monkeypatch, model, explicit):
    monkeypatch.setitem(cli.A.PROVIDERS, "ollama", {"base_url": "http://localhost:11434/v1",
                                                    "model": model})
    monkeypatch.setattr(cli.A, "ACTIVE_PROVIDER", "ollama")
    monkeypatch.setattr(cli.A, "MODEL_NAME", model)
    monkeypatch.setattr(cli.A, "active_endpoint_url",
                        lambda provider_name="": "http://localhost:11434/v1")
    if explicit:
        monkeypatch.setitem(cli.A.APP_CONFIG, "provider.ollama.model", model)
    else:
        monkeypatch.delitem(cli.A.APP_CONFIG, "provider.ollama.model", raising=False)
    monkeypatch.setattr(cli.A.local_models, "pick_installed_ollama_model",
                        lambda base_url, preferred, timeout=2.0: "llama3:latest")


def test_an_unpulled_default_ollama_model_is_swapped_for_this_session(monkeypatch):
    _ollama_active(monkeypatch, "qwen14b-tooluse-v3", explicit=False)
    persisted = []
    monkeypatch.setattr(cli.A, "update_simple_config", lambda *a, **k: persisted.append(a))
    lines = cli._startup_ollama_model_check()
    assert cli.A.MODEL_NAME == "llama3:latest"
    assert lines == ["model qwen14b-tooluse-v3 isn't pulled; using llama3:latest "
                     "(pull qwen14b-tooluse-v3 with `ollama pull qwen14b-tooluse-v3`)."]
    assert persisted == []  # session only, never written to config


def test_an_explicitly_configured_ollama_model_is_only_warned_about(monkeypatch):
    _ollama_active(monkeypatch, "my-model", explicit=True)
    lines = cli._startup_ollama_model_check()
    assert cli.A.MODEL_NAME == "my-model"
    assert "ollama pull my-model" in lines[0]


def test_the_ollama_check_skips_hosted_providers(monkeypatch):
    monkeypatch.setattr(cli.A, "ACTIVE_PROVIDER", "openai")
    monkeypatch.setattr(cli.A, "active_endpoint_url", lambda provider_name="": "https://api.openai.com/v1")
    monkeypatch.setattr(cli.A.local_models, "pick_installed_ollama_model",
                        lambda *a, **k: pytest.fail("probed a hosted provider"))
    assert cli._startup_ollama_model_check() == []


# --- first run ------------------------------------------------------------------

def _no_provider(monkeypatch):
    monkeypatch.delitem(cli.A.APP_CONFIG, "default_provider", raising=False)
    monkeypatch.delenv("AGENT8088_PROVIDER", raising=False)
    monkeypatch.setattr(cli, "_user_configured_providers", lambda: set())


def test_first_run_with_a_dead_endpoint_prints_the_setup_hint_when_not_interactive(monkeypatch, out):
    _no_provider(monkeypatch)
    monkeypatch.setattr(cli, "_endpoint_probe", lambda e: "unreachable (refused)")
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False, raising=False)
    monkeypatch.setattr(cli, "_run_setup", lambda **k: pytest.fail("ran setup unasked"))
    cli._first_run_check()
    assert "Run `agent8088 --setup`" in out.getvalue()


def test_first_run_offers_setup_on_a_terminal(monkeypatch, out):
    _no_provider(monkeypatch)
    monkeypatch.setattr(cli, "_endpoint_probe", lambda e: "unreachable (refused)")
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(cli.console, "input", lambda prompt="": "")
    ran = []
    monkeypatch.setattr(cli, "_run_setup", lambda **k: ran.append(k))
    cli._first_run_check()
    assert ran == [{"activate_runtime": True}]


def test_a_configured_provider_never_probes(monkeypatch):
    monkeypatch.setitem(cli.A.APP_CONFIG, "default_provider", "openai")
    monkeypatch.setattr(cli, "_endpoint_probe", lambda e: pytest.fail("probed at startup"))
    cli._first_run_check()


def test_a_reachable_default_endpoint_says_nothing(monkeypatch, out):
    _no_provider(monkeypatch)
    monkeypatch.setattr(cli, "_endpoint_probe", lambda e: "reachable (localhost:11434)")
    cli._first_run_check()
    assert out.getvalue() == ""


# --- /models, /model ------------------------------------------------------------

def test_an_offline_model_list_is_labelled_with_the_reason(monkeypatch, out):
    from agent8088 import providers

    class _Client:
        models = object()

    monkeypatch.setattr(cli.A, "get_client", lambda provider=None: (_Client(), "m"))

    def failing_list(provider, client=None, fallback=True):
        providers.LAST_LIST_ERROR[provider] = ConnectionRefusedError("connection refused")
        return ["gpt-4o"]

    monkeypatch.setattr(providers, "list_models", failing_list)
    models = cli._fetch_models_for_provider("openai")
    note = cli._model_discovery_note("openai", models)
    assert models == ["gpt-4o"]
    assert note.startswith("(offline list — discovery failed: Can't reach")


def test_a_live_model_list_has_no_label(monkeypatch):
    from agent8088 import providers

    class _Client:
        models = object()

    monkeypatch.setattr(cli.A, "get_client", lambda provider=None: (_Client(), "m"))
    monkeypatch.setattr(providers, "list_models", lambda provider, client=None, fallback=True: ["a"])
    providers.LAST_LIST_ERROR.pop("openai", None)
    assert cli._model_discovery_note("openai", cli._fetch_models_for_provider("openai")) == ""


def test_switching_to_a_provider_without_its_key_warns(monkeypatch, out):
    monkeypatch.setitem(cli.A.PROVIDERS, "openai", {"api_key_env": "OPENAI_API_KEY",
                                                    "base_url": "https://api.openai.com/v1"})
    monkeypatch.setattr(cli.A, "_provider_api_key", lambda profile: "")
    cli._warn_missing_provider_key("openai")
    assert "OPENAI_API_KEY isn't set — set it or run `agent8088 --setup`." in out.getvalue()


def test_model_table_says_when_nothing_is_configured(monkeypatch, out):
    monkeypatch.setattr(cli, "_user_configured_providers", lambda: set())
    monkeypatch.setattr(cli.A, "_provider_api_key", lambda profile: "")
    cli.cmd_model("")
    text = out.getvalue()
    assert "No provider configured yet" in text
    assert "missing" in text  # the Key column names what is absent


# --- answers, retries, stream resets ------------------------------------------

def test_could_not_answer_renders_as_an_error(out):
    cli.render_answer("I could not answer: Can't reach http://x. Run /doctor to check your setup.")
    text = out.getvalue()
    assert "error" in text.splitlines()[0]
    assert "Agent8088" not in text


class _Esc:
    _active = False

    def __init__(self):
        import threading
        self.triggered = threading.Event()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def paused(self):
        from contextlib import nullcontext
        return nullcontext()


def _chat(monkeypatch, session, run_agent):
    monkeypatch.setattr(cli, "EscListener", _Esc)
    monkeypatch.setattr(cli.A, "run_agent", run_agent)
    monkeypatch.setattr(cli, "_await_memory_capture", lambda stored: None)
    monkeypatch.setattr(cli, "_after_turn_plan_state", lambda: None)
    cli.do_chat("hello")


def test_retry_notices_are_shown_and_a_reset_discards_the_fragment(monkeypatch, out, session):
    def run_agent(messages, **kwargs):
        kwargs["on_token"]("content", "half a repl")
        kwargs["on_status"]("retrying in 2s (429) — attempt 2/4")
        kwargs["on_stream_reset"]()
        raise cli.A.AgentInterrupted()

    _chat(monkeypatch, session, run_agent)
    text = out.getvalue()
    assert "↻ retrying in 2s (429) — attempt 2/4" in text
    # The interrupted-turn path prints whatever prose is left; after the
    # reset there is none, so the fragment never reaches the transcript.
    assert "half a repl" not in text


def test_an_error_answer_from_the_loop_is_shown_as_an_error(monkeypatch, out, session):
    _chat(monkeypatch, session,
          lambda messages, **kwargs: "I could not answer: API key rejected by openai (HTTP 401).")
    assert "error" in out.getvalue()


# --- REPL exits -----------------------------------------------------------------

def _repl(monkeypatch, lines, chat=None):
    feed = iter(lines)

    def read_line():
        value = next(feed)
        if isinstance(value, BaseException):
            raise value
        return value

    monkeypatch.setattr(cli, "configure_logging", lambda: None)
    monkeypatch.setattr(cli.A, "resolve_auto_search_provider", lambda: None)
    monkeypatch.setattr(cli.A, "verify_sandbox_backend", lambda: None)
    monkeypatch.setattr(cli, "_install_completion", lambda: None)
    monkeypatch.setattr(cli, "_first_run_check", lambda: None)
    monkeypatch.setattr(cli, "_startup_ollama_model_check", lambda: [])
    monkeypatch.setattr(cli, "_startup_notices", lambda extra=(): [])
    monkeypatch.setattr(cli, "_mcp_failure_notice", lambda: [])
    monkeypatch.setattr(cli, "banner", lambda: None)
    monkeypatch.setattr(cli, "warn_about_unknown_theme", lambda: None)
    monkeypatch.setattr(cli, "_flush_memory_capture", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_read_line", read_line)
    if chat:
        monkeypatch.setattr(cli, "do_chat", chat)
    saves = []
    monkeypatch.setattr(cli, "_save_active_session", lambda: saves.append(1))
    monkeypatch.setattr(sys, "argv", ["agent8088"])
    return saves


def test_ctrl_c_mid_turn_quits_and_saves_the_session(monkeypatch, out, session):
    def chat(line):
        cli.S.messages.append({"role": "user", "content": line})
        raise KeyboardInterrupt

    saves = _repl(monkeypatch, ["a question"], chat=chat)
    cli.main()
    assert saves == [1]
    assert "bye" in out.getvalue()


@pytest.mark.parametrize("ending", [EOFError(), "/exit"])
def test_every_way_out_saves_once(monkeypatch, out, session, ending):
    saves = _repl(monkeypatch, [ending])
    cli.main()
    assert saves == [1]


def test_an_unexpected_error_still_saves(monkeypatch, out, session):
    saves = _repl(monkeypatch, [RuntimeError("boom")])
    with pytest.raises(RuntimeError):
        cli.main()
    assert saves == [1]


def test_ctrl_c_inside_a_command_cancels_it_and_keeps_the_repl(monkeypatch, out, session):
    def picker(_rest):
        raise KeyboardInterrupt

    monkeypatch.setitem(cli.COMMANDS, "models", picker)
    _repl(monkeypatch, ["/models", "/exit"])
    cli.main()
    text = out.getvalue()
    assert "cancelled" in text and "bye" in text


def test_provider_warnings_are_never_hidden_under_more(monkeypatch):
    monkeypatch.setattr(cli.A, "CONFIG_WARNINGS", [
        "config.txt: unknown setting 'a'", "config.txt: x='1s' is not a number",
        "config.txt: y='2k' is not a number",
        "Unknown provider 'olama-cloud' (did you mean 'ollama-cloud'?); using ollama."])
    lines = cli._startup_notices()
    assert any("olama-cloud" in line for line in lines[:cli.STARTUP_WARNING_LINES])
