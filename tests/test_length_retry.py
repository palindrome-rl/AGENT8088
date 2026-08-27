"""run_agent's retry after a MAX_COMPLETION_TOKENS cutoff must match the
failure mode: a reasoning-only overflow needs a 'stop thinking' nudge and a
bigger budget on retry — not the 'split large work across calls' advice and
the same unchanged budget, which just reproduces the same failure.
"""

import pytest


def _length_response(content):
    return type("R", (), {"choices": [type("C", (), {
        "message": type("M", (), {"content": content})(),
        "finish_reason": "length",
    })()]})()


def _stop_response(content):
    return type("R", (), {"choices": [type("C", (), {
        "message": type("M", (), {"content": content})(),
        "finish_reason": "stop",
    })()]})()


def test_reasoning_only_overflow_gets_stop_thinking_nudge(monkeypatch, engine):
    """finish_reason=length with NO visible content (all budget spent on an
    unclosed <think> block) must retry with an instruction to stop reasoning
    and answer immediately — not the generic 'split work into calls' text."""
    calls = []

    def _fake(messages, tools, max_tokens=None, **kw):
        calls.append({"messages": list(messages), "max_tokens": max_tokens})
        if len(calls) == 1:
            # Runaway, never-closed <think> block: _strip_reasoning drops it
            # entirely, so `content` at the cutoff is empty.
            return _length_response("<think>reasoning forever with no end")
        return _stop_response("the actual answer")

    monkeypatch.setattr(engine, "_create_completion_with_fallback", _fake)
    result = engine.run_agent([{"role": "user", "content": "its still off"}],
                              max_turns=5)

    assert len(calls) == 2, "expected exactly one automatic retry"
    retry_nudge = calls[1]["messages"][-1]["content"]
    assert "stop reasoning" in retry_nudge.lower() or "do not think" in retry_nudge.lower()
    assert "split large work" not in retry_nudge.lower()
    assert result == "the actual answer"


def test_large_answer_overflow_keeps_split_work_nudge(monkeypatch, engine):
    """finish_reason=length with SOME visible content (a real answer/tool call
    in progress) should keep today's 'split large work across calls' advice."""
    calls = []

    def _fake(messages, tools, max_tokens=None, **kw):
        calls.append({"messages": list(messages), "max_tokens": max_tokens})
        if len(calls) == 1:
            return _length_response("x" * 500)  # a real, oversized answer
        return _stop_response("the actual answer")

    monkeypatch.setattr(engine, "_create_completion_with_fallback", _fake)
    engine.run_agent([{"role": "user", "content": "hi"}], max_turns=5)

    retry_nudge = calls[1]["messages"][-1]["content"]
    assert "split large work" in retry_nudge.lower()


def test_retry_after_length_cutoff_gets_a_bigger_budget(monkeypatch, engine):
    """The one automatic retry must ask for more than the default budget, so
    a model that keeps reasoning almost as long still has room for an answer."""
    calls = []

    def _fake(messages, tools, max_tokens=None, **kw):
        calls.append(max_tokens)
        if len(calls) == 1:
            return _length_response("<think>reasoning forever with no end")
        return _stop_response("the actual answer")

    monkeypatch.setattr(engine, "_create_completion_with_fallback", _fake)
    engine.run_agent([{"role": "user", "content": "its still off"}], max_turns=5)

    assert calls[0] == engine.MAX_COMPLETION_TOKENS
    expected_retry_budget = min(engine.MAX_COMPLETION_TOKENS * 2, engine.CONTEXT_WINDOW)
    assert calls[1] == expected_retry_budget
    assert calls[1] > calls[0]


@pytest.mark.parametrize("ctx,comp", [("1048576", "131072"), ("262144", "32768")])
def test_provider_config_override_sets_token_limits(monkeypatch, engine, ctx, comp):
    """Per-provider config keys (provider.<name>.context_window / max_completion_tokens)
    are the supported way to set model-specific limits without a hardcoded catalog."""
    monkeypatch.setattr(engine, "ACTIVE_PROVIDER", "ollama-cloud")
    monkeypatch.setattr(engine, "DEFAULT_PROVIDER", "ollama-cloud")
    monkeypatch.setattr(engine, "MODEL_NAME", "glm-5.3-flash")
    engine.PROVIDERS["ollama-cloud"] = {
        "model": "glm-5.3-flash",
        "context_window": ctx,
        "max_completion_tokens": comp,
    }

    context, completion = engine._active_model_token_limits()

    assert context == int(ctx)
    assert completion == int(comp)


def test_agent_call_receives_the_active_models_completion_limit(monkeypatch, engine):
    seen = []
    monkeypatch.setattr(engine, "ACTIVE_PROVIDER", "ollama-cloud")
    monkeypatch.setattr(engine, "DEFAULT_PROVIDER", "ollama-cloud")
    monkeypatch.setattr(engine, "MODEL_NAME", "glm-5.2")
    engine.PROVIDERS["ollama-cloud"] = {
        "model": "glm-5.2",
        "context_window": "1048576",
        "max_completion_tokens": "131072",
    }

    def _fake(messages, tools, max_tokens=None, **kw):
        seen.append(max_tokens)
        return _stop_response("done")

    monkeypatch.setattr(engine, "_create_completion_with_fallback", _fake)
    result = engine.run_agent(
        [{"role": "user", "content": "build it"}], max_turns=1
    )
    assert result == "done"
    assert seen == [131_072]


def test_provider_token_override_wins_over_global_config(monkeypatch, engine):
    monkeypatch.setattr(engine, "ACTIVE_PROVIDER", "ollama-cloud")
    monkeypatch.setattr(engine, "DEFAULT_PROVIDER", "ollama-cloud")
    monkeypatch.setattr(engine, "MODEL_NAME", "glm-5.3-flash")
    engine.PROVIDERS["ollama-cloud"] = {
        "model": "glm-5.3-flash",
        "context_window": "200000",
        "max_completion_tokens": "24000",
    }
    engine.APP_CONFIG["context_window"] = "32768"
    engine.APP_CONFIG["max_completion_tokens"] = "8192"

    assert engine._active_model_token_limits() == (200_000, 24_000)


def test_probe_result_is_used_when_no_config_override(monkeypatch, engine):
    """When no per-provider or global config is set, a probed value stored in
    PROVIDERS[name] is used — this is how the endpoint probe feeds limits."""
    monkeypatch.setattr(engine, "ACTIVE_PROVIDER", "ollama-cloud")
    monkeypatch.setattr(engine, "DEFAULT_PROVIDER", "ollama-cloud")
    monkeypatch.setattr(engine, "MODEL_NAME", "kimi-k2.6")
    engine.PROVIDERS["ollama-cloud"] = {
        "model": "kimi-k2.6",
        "context_window": "262144",  # probed via /api/show, session-only
    }
    engine.APP_CONFIG.pop("context_window", None)
    engine.APP_CONFIG.pop("max_completion_tokens", None)

    context, completion = engine._active_model_token_limits()
    assert context == 262144
    # completion falls back to MAX_COMPLETION_TOKENS (probe doesn't set it)
    assert completion == engine.MAX_COMPLETION_TOKENS


def test_turn_limit_reports_error_and_latest_tool_result(monkeypatch, engine):
    responses = iter((
        '✿FUNCTION✿: read_text ✿ARGS✿: {"filename": "first"}',
        '✿FUNCTION✿: read_text ✿ARGS✿: {"filename": "second"}',
    ))
    results = iter(("old skill text", "latest failure"))

    def completion(*_args, **_kwargs):
        return _stop_response(next(responses))

    monkeypatch.setattr(engine, "_create_completion_with_fallback", completion)
    monkeypatch.setattr(engine, "exec_tool", lambda *_args, **_kwargs: next(results))

    answer = engine.run_agent(
        [{"role": "user", "content": "test"}], max_turns=2,
        system_prompt="", tools_def=[], allowed_tools={"read_text"},
    )

    assert answer.startswith("Error: Agent reached the 2-turn limit")
    assert "latest failure" in answer
    assert "old skill text" not in answer


# ---------------------------------------------------------------------------
# Gap 1: /limits provider live override (set_provider_limit)
# ---------------------------------------------------------------------------

def test_set_provider_limit_writes_to_providers_and_config(monkeypatch, tmp_path, engine):
    """set_provider_limit mutates PROVIDERS, APP_CONFIG, and config.txt."""
    monkeypatch.setattr(engine, "CONFIG_PATH", tmp_path / "config.txt")
    engine.PROVIDERS["testprov"] = {"model": "m1"}
    engine.APP_CONFIG.clear()

    result = engine.set_provider_limit("testprov", "max_completion_tokens", "50000")

    assert result["new"] == 50000
    assert engine.PROVIDERS["testprov"]["max_completion_tokens"] == "50000"
    assert engine.APP_CONFIG["provider.testprov.max_completion_tokens"] == "50000"
    assert "provider.testprov.max_completion_tokens=50000" in (tmp_path / "config.txt").read_text()


def test_set_provider_limit_rejects_unknown_provider(monkeypatch, engine):
    with pytest.raises(KeyError):
        engine.set_provider_limit("nonexistent", "context_window", "1000")


def test_set_provider_limit_rejects_unknown_key(monkeypatch, engine):
    engine.PROVIDERS["testprov2"] = {"model": "m"}
    with pytest.raises(ValueError, match="unknown provider limit"):
        engine.set_provider_limit("testprov2", "temperature", "0.5")


def test_set_provider_limit_rejects_non_positive(monkeypatch, engine):
    engine.PROVIDERS["testprov3"] = {"model": "m"}
    with pytest.raises(ValueError):
        engine.set_provider_limit("testprov3", "context_window", "0")


def test_set_provider_limit_takes_effect_next_turn(monkeypatch, tmp_path, engine):
    """After set_provider_limit, _active_model_token_limits picks up the new value."""
    monkeypatch.setattr(engine, "CONFIG_PATH", tmp_path / "config.txt")
    monkeypatch.setattr(engine, "ACTIVE_PROVIDER", "provX")
    monkeypatch.setattr(engine, "DEFAULT_PROVIDER", "provX")
    monkeypatch.setattr(engine, "MODEL_NAME", "m")
    engine.PROVIDERS["provX"] = {"model": "m"}

    engine.set_provider_limit("provX", "context_window", "500000")
    engine.set_provider_limit("provX", "max_completion_tokens", "60000")

    _ctx_after, _out_after = engine._active_model_token_limits()
    assert _ctx_after == 500000
    assert _out_after == 60000


# ---------------------------------------------------------------------------
# Gap 2: best-effort endpoint probe (probe_model_context_window + _maybe_probe)
# ---------------------------------------------------------------------------

def test_probe_finds_context_window_on_model_object():
    """probe_model_context_window reads a non-standard context_window field
    from /v1/models (the OpenAI-compatible fallback path)."""
    from agent8088 import providers

    class _M:
        id = "test-model"
        context_window = 256000

    class _Resp:
        data = [_M()]

    class _Client:
        base_url = "http://non-ollama.example.com/v1"
        api_key = ""
        class models:
            @staticmethod
            def list():
                return _Resp()

    result = providers.probe_model_context_window(_Client(), "test-model", provider_name="custom")
    assert result == 256000


def test_probe_returns_none_when_no_field():
    from agent8088 import providers

    class _M:
        id = "no-ctx-model"

    class _Resp:
        data = [_M()]

    class _Client:
        base_url = "http://non-ollama.example.com/v1"
        api_key = ""
        class models:
            @staticmethod
            def list():
                return _Resp()

    result = providers.probe_model_context_window(_Client(), "no-ctx-model", provider_name="custom")
    assert result is None


def test_probe_returns_none_on_endpoint_error():
    from agent8088 import providers

    class _Client:
        base_url = "http://non-ollama.example.com/v1"
        api_key = ""
        class models:
            @staticmethod
            def list():
                raise ConnectionError("endpoint down")

    result = providers.probe_model_context_window(_Client(), "any-model", provider_name="custom")
    assert result is None


def test_probe_uses_ollama_api_show_for_llama_provider(monkeypatch):
    """For ollama/ollama-cloud providers, the probe queries /api/show and reads
    model_info['<arch>.context_length']."""
    import httpx as _real_httpx
    from agent8088 import providers

    class _FakeResponse:
        status_code = 200
        def json(self):
            return {"model_info": {"llama.context_length": 131072}}

    def _fake_post(url, **kwargs):
        assert "/api/show" in url
        return _FakeResponse()

    monkeypatch.setattr(_real_httpx, "post", _fake_post)

    class _Client:
        base_url = "http://localhost:11434/v1"
        api_key = "ollama"

    result = providers.probe_model_context_window(_Client(), "llama3.3", provider_name="ollama")
    assert result == 131072


def test_maybe_probe_stores_in_providers_session_only(monkeypatch, engine):
    """_maybe_probe_context_window stores a probed value in PROVIDERS but
    does NOT persist to config.txt."""
    monkeypatch.setattr(engine, "ACTIVE_PROVIDER", "probeprov")
    monkeypatch.setattr(engine, "DEFAULT_PROVIDER", "probeprov")
    monkeypatch.setattr(engine, "MODEL_NAME", "probed-model")
    engine.PROVIDERS["probeprov"] = {"model": "probed-model"}
    engine.APP_CONFIG.pop("context_window", None)

    class _FakeClient:
        pass

    def _fake_probe(client, model_id, provider_name="", timeout=5):
        return 786432

    monkeypatch.setattr(engine, "client", _FakeClient())
    import agent8088.providers as _prov
    monkeypatch.setattr(_prov, "probe_model_context_window", _fake_probe)

    engine._maybe_probe_context_window()

    assert engine.PROVIDERS["probeprov"]["context_window"] == "786432"


def test_maybe_probe_skips_when_context_already_set(monkeypatch, engine):
    """If the provider already has context_window, no probe runs."""
    monkeypatch.setattr(engine, "ACTIVE_PROVIDER", "hasctx")
    monkeypatch.setattr(engine, "DEFAULT_PROVIDER", "hasctx")
    monkeypatch.setattr(engine, "MODEL_NAME", "m")
    engine.PROVIDERS["hasctx"] = {"model": "m", "context_window": "999999"}

    probe_called = []
    import agent8088.providers as _prov
    monkeypatch.setattr(_prov, "probe_model_context_window",
                        lambda *a, **kw: probe_called.append(1) or 123)

    engine._maybe_probe_context_window()

    assert probe_called == []  # probe was not called
    assert engine.PROVIDERS["hasctx"]["context_window"] == "999999"


# ---------------------------------------------------------------------------
# Gap 3: CWD config.txt isolation
# ---------------------------------------------------------------------------

def test_cwd_config_is_selected_when_present(monkeypatch, tmp_path):
    """When ./config.txt exists in CWD, it is used exclusively (no global)."""
    import importlib
    import sys
    from pathlib import Path

    local_config = tmp_path / "config.txt"
    local_config.write_text("default_provider=localprov\nmodel_name=local-model\n")

    monkeypatch.delenv("AGENT8088_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    from agent8088 import engine as mod
    reloaded = importlib.reload(mod)

    assert reloaded.CONFIG_PATH.resolve() == local_config.resolve()
    assert reloaded.APP_CONFIG.get("default_provider") == "localprov"
    assert reloaded.APP_CONFIG.get("model_name") == "local-model"


def test_cwd_config_isolated_from_global(monkeypatch, tmp_path):
    """/limits provider writes go to the CWD config, not the global."""
    import importlib
    import sys
    from pathlib import Path

    local_config = tmp_path / "config.txt"
    local_config.write_text("default_provider=localprov\n")

    monkeypatch.delenv("AGENT8088_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    from agent8088 import engine as mod
    reloaded = importlib.reload(mod)

    reloaded.PROVIDERS["localprov"] = {"model": "local-model"}
    reloaded.set_provider_limit("localprov", "context_window", "200000")

    content = local_config.read_text()
    assert "provider.localprov.context_window=200000" in content
