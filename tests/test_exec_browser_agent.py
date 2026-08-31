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

    async def fake_run_browser_agent(url, task, executable_path=None):
        calls.append((url, task, executable_path))
        return "The heading says Hello."

    monkeypatch.setattr(A, "_run_browser_agent", fake_run_browser_agent)

    result = A._exec_browser({"url": "https://example.com", "task": "read the heading"})

    assert calls == [("https://example.com", "read the heading", str(tmp_path / "chrome.exe"))]
    assert "The heading says Hello." in result
    assert "<<<EXTERNAL_UNTRUSTED_CONTENT" in result


def test_sets_and_restores_active_role_around_the_run(monkeypatch, tmp_path):
    _install_present_chromium(monkeypatch, tmp_path)
    seen_role = {}

    async def fake_run_browser_agent(url, task, executable_path=None):
        seen_role["during"] = A._active_role
        return "ok"

    monkeypatch.setattr(A, "_run_browser_agent", fake_run_browser_agent)
    monkeypatch.setattr(A, "_active_role", "main")

    A._exec_browser({"url": "https://example.com", "task": "read the heading"})

    assert seen_role["during"] == "subagent:browser"
    assert A._active_role == "main"


def test_active_role_restored_even_when_the_run_raises(monkeypatch, tmp_path):
    _install_present_chromium(monkeypatch, tmp_path)

    async def fake_run_browser_agent(url, task, executable_path=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(A, "_run_browser_agent", fake_run_browser_agent)
    monkeypatch.setattr(A, "_active_role", "main")

    result = A._exec_browser({"url": "https://example.com", "task": "read the heading"})

    assert "Browser error" in result
    assert A._active_role == "main"


def test_ctrl_c_during_the_run_is_reraised_not_swallowed(monkeypatch, tmp_path):
    """Ctrl+C must still end agent8088 (cli.py's main loop catches this one
    level up) - this only silences the cosmetic asyncio shutdown noise that
    follows, it must never turn the interrupt itself into a quiet return."""
    _install_present_chromium(monkeypatch, tmp_path)

    async def fake_run_browser_agent(url, task, executable_path=None):
        raise KeyboardInterrupt()

    monkeypatch.setattr(A, "_run_browser_agent", fake_run_browser_agent)

    with pytest.raises(KeyboardInterrupt):
        A._exec_browser({"url": "https://example.com", "task": "read the heading"})


def test_ctrl_c_during_the_run_silences_the_asyncio_shutdown_warning(monkeypatch, tmp_path):
    """asyncio.run() can't always finish gracefully cancelling a mid-flight
    Playwright session on a hard interrupt - the interpreter's later garbage
    collection of the abandoned task then logs "Task was destroyed but it
    is pending!" through the standard "asyncio" logger, well after the CLI
    has already said goodbye. The process is exiting either way, so that
    logger should go quiet rather than print what reads as a crash on the
    way out of a process that already exited cleanly."""
    import logging
    _install_present_chromium(monkeypatch, tmp_path)
    logger = logging.getLogger("asyncio")
    original_level = logger.level
    logger.setLevel(logging.NOTSET)

    async def fake_run_browser_agent(url, task, executable_path=None):
        raise KeyboardInterrupt()

    monkeypatch.setattr(A, "_run_browser_agent", fake_run_browser_agent)

    try:
        with pytest.raises(KeyboardInterrupt):
            A._exec_browser({"url": "https://example.com", "task": "read the heading"})
        assert logger.level == logging.CRITICAL
    finally:
        logger.setLevel(original_level)


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
    close_raises = None  # set by the close-failure test to make close() fail

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
        if _FakeAgent.close_raises is not None:
            raise _FakeAgent.close_raises
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
    _FakeAgent.close_raises = None
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


_NOISY_LOGGER_NAMES = ("browser_use", "bubus", "LiteLLM")


def test_set_browser_use_log_verbosity_quiets_every_noisy_logger_by_default():
    """"bubus" specifically: browser-use's own "result"/quiet logging mode
    explicitly special-cases this logger to stay at INFO regardless (it
    carries the Agent's step narration, dispatched through its event bus) -
    a real bug that let the "quiet by default" fix ship without actually
    silencing the noise it was meant to hide. Set directly, not through
    browser-use's own setup_logging(), so this doesn't depend on it ever
    fixing that."""
    import logging
    loggers = [logging.getLogger(name) for name in _NOISY_LOGGER_NAMES]
    original_levels = [logger.level for logger in loggers]
    try:
        A._set_browser_use_log_verbosity(False)
        for logger in loggers:
            assert logger.level == logging.WARNING, logger.name
    finally:
        for logger, level in zip(loggers, original_levels):
            logger.setLevel(level)


def test_set_browser_use_log_verbosity_restores_info_on_every_logger_when_verbose():
    import logging
    loggers = [logging.getLogger(name) for name in _NOISY_LOGGER_NAMES]
    original_levels = [logger.level for logger in loggers]
    try:
        A._set_browser_use_log_verbosity(True)
        for logger in loggers:
            assert logger.level == logging.INFO, logger.name
    finally:
        for logger, level in zip(loggers, original_levels):
            logger.setLevel(level)


def test_set_browser_use_log_verbosity_silences_asyncios_shutdown_noise_by_default():
    """Not gated behind catching a KeyboardInterrupt: browser-use's own
    cleanup of a Playwright session's low-level connection task can leave it
    lingering even on a normal, successful completion, and the interpreter's
    later garbage collection of it logs through the standard "asyncio"
    logger at ERROR level - WARNING (the threshold the other three loggers
    use) would not suppress that, only CRITICAL does."""
    import logging
    logger = logging.getLogger("asyncio")
    original_level = logger.level
    try:
        A._set_browser_use_log_verbosity(False)
        assert logger.level == logging.CRITICAL
    finally:
        logger.setLevel(original_level)


def test_set_browser_use_log_verbosity_restores_asyncio_warnings_when_verbose():
    import logging
    logger = logging.getLogger("asyncio")
    original_level = logger.level
    try:
        A._set_browser_use_log_verbosity(True)
        assert logger.level == logging.WARNING
    finally:
        logger.setLevel(original_level)


def _log_record(message):
    import logging
    return logging.LogRecord(
        name="browser_use.browser.watchdogs.security_watchdog", level=logging.WARNING,
        pathname=__file__, lineno=1, msg=message, args=(), exc_info=None)


def test_noise_filter_drops_the_glob_pattern_notice():
    filt = A._QuietBrowserUseNoiseFilter()
    record = _log_record(
        '⚠️ Using glob patterns in allowed_domains. Note: Patterns like '
        '"*.example.com" will match both subdomains AND the main domain.')
    assert filt.filter(record) is False


def test_noise_filter_drops_the_empty_action_retry_notice():
    filt = A._QuietBrowserUseNoiseFilter()
    assert filt.filter(_log_record("Model returned empty action. Retrying...")) is False
    assert filt.filter(_log_record("Model still returned empty after retry. Inserting safe noop action.")) is False


def test_noise_filter_keeps_security_blocking_warnings():
    """The exact scenario this must never hide: the SSRF deny-list's second
    layer (see the Critical fix) actually refusing a navigation. Losing
    visibility into that would be a real transparency regression, not a
    cosmetic one."""
    filt = A._QuietBrowserUseNoiseFilter()
    assert filt.filter(_log_record(
        "⛔️ Blocking navigation to disallowed URL: http://169.254.169.254/")) is True
    assert filt.filter(_log_record(
        "⛔️ Navigation to non-allowed URL detected: http://127.0.0.1:9/")) is True


@pytest.fixture
def browser_use_handler():
    """pytest's own logging plugin clears the "browser_use" logger's
    handlers before each test runs, so the handler a plain `import
    browser_use` would normally attach at package-init time isn't there to
    inspect by the time a test starts. Attach a stand-in explicitly instead
    - _set_browser_use_log_verbosity only cares that it's a handler on this
    logger, not that browser-use created it."""
    import logging
    logger = logging.getLogger("browser_use")
    handler = logging.NullHandler()
    logger.addHandler(handler)
    try:
        yield handler
    finally:
        logger.removeHandler(handler)


def test_verbosity_toggle_adds_and_removes_the_noise_filter_on_the_handler(browser_use_handler):
    A._set_browser_use_log_verbosity(False)
    assert A._browser_use_noise_filter in browser_use_handler.filters
    A._set_browser_use_log_verbosity(True)
    assert A._browser_use_noise_filter not in browser_use_handler.filters


def test_verbosity_toggle_does_not_duplicate_the_filter_across_repeated_calls(browser_use_handler):
    for _ in range(5):
        A._set_browser_use_log_verbosity(False)
    assert browser_use_handler.filters.count(A._browser_use_noise_filter) == 1


def test_set_browser_use_log_verbosity_does_not_accumulate_handlers():
    """The earlier implementation called browser-use's own setup_logging()
    on every browse_page call, which appends a fresh handler to the
    "browser_use"/"bubus" loggers each time (only the root logger's handlers
    get cleared) - after enough calls in one long session, every log line
    would print once per accumulated handler. Setting levels directly must
    never touch handlers at all."""
    import logging
    loggers = [logging.getLogger(name) for name in ("browser_use", "bubus")]
    handler_counts_before = [len(logger.handlers) for logger in loggers]
    for _ in range(5):
        A._set_browser_use_log_verbosity(False)
        A._set_browser_use_log_verbosity(True)
    for logger, before in zip(loggers, handler_counts_before):
        assert len(logger.handlers) == before, logger.name


def test_the_profile_is_built_from_the_security_kwargs(fake_browser_use):
    asyncio.run(A._run_browser_agent("https://example.com", "read the page"))

    kwargs = fake_browser_use.profiles[0]
    assert kwargs["headless"] is True
    assert kwargs["proxy"].bypass == "<-loopback>"
    assert kwargs["proxy"].server.startswith("http://127.0.0.1:")
    assert kwargs["prohibited_domains"]


def test_the_agent_is_built_without_vision_thinking_or_the_judge(fake_browser_use):
    """use_vision=False: browser-use's default (True) sends a screenshot
    every step and hard-errors against a model that doesn't accept image
    input - this adapter has to work with whatever model is configured, not
    assume one that can see. use_judge=False: browser-use's default (True)
    runs one extra full LLM call after every completed task purely to
    self-critique the result (advisory-only - its verdict doesn't retry or
    change what's returned), which is pure extra latency/cost, and whose
    rubric leans on screenshot evidence that use_vision=False guarantees
    will never exist - so left on, it reliably reports correct answers as
    failed. Neither flag had a regression test before this."""
    asyncio.run(A._run_browser_agent("https://example.com", "read the page"))

    kwargs = fake_browser_use.agents[0].kwargs
    assert kwargs["use_vision"] is False
    assert kwargs["use_thinking"] is False
    assert kwargs["llm_timeout"] == A.TIMEOUT_SECONDS
    assert kwargs["max_actions_per_step"] == A.BROWSER_MAX_ACTIONS_PER_STEP
    assert "current browser state" in kwargs["extend_system_message"]
    assert kwargs["use_judge"] is False


# --- browser_max_actions_per_step --------------------------------------------
# Measured on a local 35B: prefill runs ~1250 tok/s and llama.cpp prefix-caches
# the (fixed, ~30k char) system prompt, but generation runs ~68 tok/s - so a
# step costs roughly its output tokens, and wall clock scales with the number
# of steps. Pinning one action per step therefore multiplies the cost of any
# multi-action task (a 7-field form went ~9 steps). It stays the default for
# reliability, but has to be tunable for a form-heavy run.

def test_the_batch_size_default_stays_one_for_reliability():
    assert A.BROWSER_MAX_ACTIONS_PER_STEP == 1


def test_config_can_raise_the_batch_size(fake_browser_use, monkeypatch):
    monkeypatch.setattr(A, "BROWSER_MAX_ACTIONS_PER_STEP", 4, raising=False)

    asyncio.run(A._run_browser_agent("https://example.com", "fill the form"))

    assert fake_browser_use.agents[0].kwargs["max_actions_per_step"] == 4


def test_env_overrides_the_batch_size(fake_browser_use, monkeypatch):
    monkeypatch.setattr(A, "BROWSER_MAX_ACTIONS_PER_STEP", 1, raising=False)
    monkeypatch.setenv("AGENT8088_BROWSER_MAX_ACTIONS_PER_STEP", "5")

    asyncio.run(A._run_browser_agent("https://example.com", "fill the form"))

    assert fake_browser_use.agents[0].kwargs["max_actions_per_step"] == 5


def test_a_nonsense_batch_size_falls_back_rather_than_crashing(
        fake_browser_use, monkeypatch):
    """A bad value must not take out a browsing run mid-demo."""
    monkeypatch.setattr(A, "BROWSER_MAX_ACTIONS_PER_STEP", 3, raising=False)
    monkeypatch.setenv("AGENT8088_BROWSER_MAX_ACTIONS_PER_STEP", "lots")

    asyncio.run(A._run_browser_agent("https://example.com", "fill the form"))

    assert fake_browser_use.agents[0].kwargs["max_actions_per_step"] == 3


def test_the_batch_size_is_never_below_one(fake_browser_use, monkeypatch):
    monkeypatch.setattr(A, "BROWSER_MAX_ACTIONS_PER_STEP", 1, raising=False)
    monkeypatch.setenv("AGENT8088_BROWSER_MAX_ACTIONS_PER_STEP", "0")

    asyncio.run(A._run_browser_agent("https://example.com", "fill the form"))

    assert fake_browser_use.agents[0].kwargs["max_actions_per_step"] == 1


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


def test_a_failed_agent_close_is_logged_not_silently_swallowed(fake_browser_use, caplog):
    """The old code caught any close() failure with a bare `except Exception:
    pass` - a wedged Chromium process could survive past the 30s timeout with
    zero visibility that cleanup didn't finish. It must at least be logged."""
    import logging
    _FakeAgent.close_raises = RuntimeError("close boom")

    with caplog.at_level(logging.WARNING, logger="agent8088.engine"):
        asyncio.run(A._run_browser_agent("https://example.com", "read the page"))

    assert any("close" in r.message and "close boom" in r.message
               for r in caplog.records)


def test_the_browser_profile_temp_dir_is_removed_after_a_normal_run(fake_browser_use):
    """BrowserProfile's own validator silently mkdtemp()s a user-data-dir
    whenever one isn't passed in, and browser-use's own cleanup only matches
    a different temp-dir prefix ('browseruse-tmp-', not
    'browser-use-user-data-dir-') - so that directory is never removed and
    every browse_page call leaks a few MB on disk. Passing an explicit dir
    means _run_browser_agent owns the directory's lifecycle and can actually
    delete it once the run is done."""
    asyncio.run(A._run_browser_agent("https://example.com", "read the page"))

    kwargs = fake_browser_use.profiles[0]
    assert kwargs.get("user_data_dir")
    assert not os.path.exists(kwargs["user_data_dir"])


def test_the_browser_profile_temp_dir_is_removed_even_when_the_task_times_out(
        fake_browser_use, monkeypatch):
    monkeypatch.setattr(A, "BROWSER_TASK_TIMEOUT_SECONDS", 1)
    _FakeAgent.hang = True

    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(A._run_browser_agent("https://example.com", "read the page"))

    kwargs = fake_browser_use.profiles[0]
    assert kwargs.get("user_data_dir")
    assert not os.path.exists(kwargs["user_data_dir"])


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
