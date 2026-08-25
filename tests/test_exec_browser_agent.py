"""_exec_browser drives an interactive browser-use Agent instead of a single
page.goto()+read. These tests stub _run_browser_agent (the async helper that
actually talks to browser-use) so they exercise _exec_browser's own argument
validation, pre-flight checks, role/budget bookkeeping, and output wrapping
without needing a real browser or model.

The last group stubs one level deeper - browser_use's own Agent/BrowserProfile
- to cover what _run_browser_agent does *around* the run: the telemetry
opt-out, the timeout clamp, and browser teardown. Still no real browser, no
real model, no network."""
import asyncio
import os
import sys
import types

import pytest

from agent8088 import engine as A

pytest.importorskip("browser_use")


class _FakeChromium:
    def __init__(self, executable_path):
        self.executable_path = executable_path


class _FakePlaywrightSession:
    def __init__(self, executable_path):
        self.chromium = _FakeChromium(executable_path)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _install_present_chromium(monkeypatch, tmp_path):
    present_path = tmp_path / "chrome.exe"
    present_path.write_text("stub")
    fake_module = types.SimpleNamespace(
        sync_playwright=lambda: _FakePlaywrightSession(str(present_path)))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_module)
    monkeypatch.setattr(A, "_playwright_available", lambda: True)
    monkeypatch.setattr(A, "_egress_check", lambda url: None)
    monkeypatch.setattr(A, "_ssrf_check", lambda url: None)


def test_missing_task_is_a_clean_error(monkeypatch, tmp_path):
    _install_present_chromium(monkeypatch, tmp_path)

    result = A._exec_browser({"url": "https://example.com"})

    assert result == "Error: browser tool requires 'task'."


def test_runs_the_browser_agent_and_wraps_the_result(monkeypatch, tmp_path):
    _install_present_chromium(monkeypatch, tmp_path)
    calls = []

    async def fake_run_browser_agent(url, task):
        calls.append((url, task))
        return "The heading says Hello."

    monkeypatch.setattr(A, "_run_browser_agent", fake_run_browser_agent)

    result = A._exec_browser({"url": "https://example.com", "task": "read the heading"})

    assert calls == [("https://example.com", "read the heading")]
    assert "The heading says Hello." in result
    assert "<<<EXTERNAL_UNTRUSTED_CONTENT" in result


def test_sets_and_restores_active_role_around_the_run(monkeypatch, tmp_path):
    _install_present_chromium(monkeypatch, tmp_path)
    seen_role = {}

    async def fake_run_browser_agent(url, task):
        seen_role["during"] = A._active_role
        return "ok"

    monkeypatch.setattr(A, "_run_browser_agent", fake_run_browser_agent)
    monkeypatch.setattr(A, "_active_role", "main")

    A._exec_browser({"url": "https://example.com", "task": "read the heading"})

    assert seen_role["during"] == "subagent:browser"
    assert A._active_role == "main"


def test_active_role_restored_even_when_the_run_raises(monkeypatch, tmp_path):
    _install_present_chromium(monkeypatch, tmp_path)

    async def fake_run_browser_agent(url, task):
        raise RuntimeError("boom")

    monkeypatch.setattr(A, "_run_browser_agent", fake_run_browser_agent)
    monkeypatch.setattr(A, "_active_role", "main")

    result = A._exec_browser({"url": "https://example.com", "task": "read the heading"})

    assert "Browser error" in result
    assert A._active_role == "main"


# --- _run_browser_agent itself, with browser-use stubbed out ----------------


class _FakeHistory:
    def __init__(self, text="done"):
        self._text = text

    def final_result(self):
        return self._text

    def is_done(self):
        return True


class _FakeAgent:
    """Stands in for browser_use.Agent: records how it was constructed, never
    launches anything."""

    instances = []
    hang = False  # set by the timeout test to make run() never return

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.closed = False
        _FakeAgent.instances.append(self)

    async def run(self, max_steps=None):
        self.max_steps = max_steps
        if _FakeAgent.hang:
            await asyncio.sleep(3600)
        return _FakeHistory()

    async def close(self):
        self.closed = True


@pytest.fixture
def fake_browser_use(monkeypatch):
    """Swap browser_use's Agent/BrowserProfile and the LLM builder for stubs,
    leaving _run_browser_agent's own logic (env vars, profile kwargs, timeout,
    teardown) fully real. The real browser_use package stays importable -
    _browser_profile_kwargs still builds a genuine ProxySettings."""
    import browser_use

    _FakeAgent.instances = []
    _FakeAgent.hang = False
    profiles = []
    llm_calls = []

    def fake_profile(**kwargs):
        profiles.append(kwargs)
        return types.SimpleNamespace(**kwargs)

    def fake_build_browser_chat_model(client, model_name, budget=None, max_tokens=None):
        llm_calls.append({"client": client, "model_name": model_name,
                          "budget": budget, "max_tokens": max_tokens})
        return object()

    monkeypatch.setattr(browser_use, "Agent", _FakeAgent)
    monkeypatch.setattr(browser_use, "BrowserProfile", fake_profile)
    monkeypatch.setattr("agent8088.browser_llm.build_browser_chat_model",
                        fake_build_browser_chat_model)
    monkeypatch.setattr(A, "_active_budget", None)
    return types.SimpleNamespace(
        profiles=profiles, agents=_FakeAgent.instances, llm_calls=llm_calls)


def test_telemetry_and_cloud_sync_are_disabled_before_the_agent_runs(
        fake_browser_use, monkeypatch):
    """browser-use posts task text, visited URLs and extracted content to a
    third-party analytics endpoint by default - traffic the egress guard and
    the audit log never see, because browser-use sends it directly."""
    monkeypatch.delenv("ANONYMIZED_TELEMETRY", raising=False)
    monkeypatch.delenv("BROWSER_USE_CLOUD_SYNC", raising=False)

    asyncio.run(A._run_browser_agent("https://example.com", "read the page"))

    assert os.environ["ANONYMIZED_TELEMETRY"] == "false"
    assert os.environ["BROWSER_USE_CLOUD_SYNC"] == "false"


def test_an_explicit_telemetry_opt_in_is_respected(fake_browser_use, monkeypatch):
    """setdefault, not assignment: an operator who deliberately turned
    telemetry on in their environment keeps it."""
    monkeypatch.setenv("ANONYMIZED_TELEMETRY", "true")

    asyncio.run(A._run_browser_agent("https://example.com", "read the page"))

    assert os.environ["ANONYMIZED_TELEMETRY"] == "true"


def test_browser_use_logging_defaults_to_quiet(fake_browser_use, monkeypatch):
    """browser-use's own step-by-step log (Eval/Memory/Next goal/...) prints
    straight to the console, redundant with the properly-formatted answer
    _run_browser_agent already returns - quiet by default, same as the main
    loop's own hidden chain-of-thought."""
    calls = []
    monkeypatch.setattr(A, "_set_browser_use_log_verbosity", lambda verbose: calls.append(verbose))
    monkeypatch.setattr(A, "SHOW_REASONING", False)

    asyncio.run(A._run_browser_agent("https://example.com", "read the page"))

    assert calls == [False]


def test_browser_use_logging_follows_the_reasoning_toggle(fake_browser_use, monkeypatch):
    """/reasoning on (aliased /think on) restores it - same toggle that
    controls the main loop's own thinking display."""
    calls = []
    monkeypatch.setattr(A, "_set_browser_use_log_verbosity", lambda verbose: calls.append(verbose))
    monkeypatch.setattr(A, "SHOW_REASONING", True)

    asyncio.run(A._run_browser_agent("https://example.com", "read the page"))

    assert calls == [True]


def test_set_browser_use_log_verbosity_quiets_litellm_by_default():
    import logging
    logger = logging.getLogger("LiteLLM")
    original_level = logger.level
    try:
        A._set_browser_use_log_verbosity(False)
        assert logger.level == logging.WARNING
    finally:
        logger.setLevel(original_level)


def test_set_browser_use_log_verbosity_restores_info_when_verbose():
    import logging
    logger = logging.getLogger("LiteLLM")
    original_level = logger.level
    try:
        A._set_browser_use_log_verbosity(True)
        assert logger.level == logging.INFO
    finally:
        logger.setLevel(original_level)


def test_the_profile_is_built_from_the_security_kwargs(fake_browser_use):
    asyncio.run(A._run_browser_agent("https://example.com", "read the page"))

    kwargs = fake_browser_use.profiles[0]
    assert kwargs["headless"] is True
    assert kwargs["proxy"].bypass == "<-loopback>"
    assert kwargs["proxy"].server.startswith("http://127.0.0.1:")
    assert kwargs["prohibited_domains"]


def test_the_llm_is_built_with_the_same_completion_token_ceiling_as_the_main_loop(
        fake_browser_use):
    """Left at ChatLiteLLM's own default (4096, half of engine.py's own 8192),
    a model that spends much of its budget on 'thinking' before writing the
    action can get cut off mid-response - browser-use reports this as "Model
    returned empty action" and retries the whole step, a silent, avoidable
    source of wasted round-trips. The browsing loop must get the same ceiling
    the main agent loop already uses, not browser-use's unrelated default."""
    asyncio.run(A._run_browser_agent("https://example.com", "read the page"))

    assert fake_browser_use.llm_calls[0]["max_tokens"] == A.MAX_COMPLETION_TOKENS


def test_the_browser_is_closed_when_the_task_times_out(fake_browser_use, monkeypatch):
    """A timed-out run is cancelled mid-step; without an explicit close() the
    Chromium process it launched would be orphaned."""
    monkeypatch.setattr(A, "BROWSER_TASK_TIMEOUT_SECONDS", 1)
    _FakeAgent.hang = True

    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(A._run_browser_agent("https://example.com", "read the page"))

    assert fake_browser_use.agents[0].closed is True


def test_the_task_timeout_is_clamped_to_the_tool_timeout_ceiling(monkeypatch):
    monkeypatch.setattr(A, "BROWSER_TASK_TIMEOUT_SECONDS", 9000)
    monkeypatch.setattr(A, "MAX_TOOL_TIMEOUT_SECONDS", 300)

    assert A._browser_task_timeout() == 300

    monkeypatch.setattr(A, "BROWSER_TASK_TIMEOUT_SECONDS", 60)

    assert A._browser_task_timeout() == 60


def test_a_budget_stop_is_reported_in_the_result(fake_browser_use, monkeypatch):
    """browser-use swallows per-step exceptions, so the RuntimeError raised by
    Agent8088ChatModel when the turn budget runs out never reaches the user on
    its own - the reason has to be re-attached to the result."""
    monkeypatch.setattr(A, "_active_budget", types.SimpleNamespace(
        exceeded=lambda: "Turn budget exceeded: 100 tokens used (limit 100)."))

    result = asyncio.run(A._run_browser_agent("https://example.com", "read the page"))

    assert "Turn budget exceeded" in result
