"""Recovery uses the selected model's limits and never starts work after expiry."""
import json
from types import SimpleNamespace as N

import pytest


class Failure(RuntimeError):
    def __init__(self, status, text):
        super().__init__(text)
        self.status_code = status


def response(text="ok", tool=None):
    calls = [N(function=N(name=tool[0], arguments=json.dumps(tool[1])))] if tool else None
    return N(choices=[N(message=N(content=text, tool_calls=calls), finish_reason="stop")], usage=None)


@pytest.fixture
def api(engine, monkeypatch):
    calls = []
    profiles = {
        "primary": {"model": "large", "context_window": "100000", "max_completion_tokens": "8192", "api_key": "primary-key"},
        "backup": {"model": "small", "context_window": "1024", "max_completion_tokens": "128", "api_key": "backup-key"},
    }
    monkeypatch.setattr(engine, "PROVIDERS", profiles)
    monkeypatch.setattr(engine, "MODEL_TELEMETRY_ENABLED", False)
    monkeypatch.setattr(engine, "API_MAX_RETRIES", 0)
    monkeypatch.setitem(engine.APP_CONFIG, "fallback_models", "backup:small")

    def client(name, body):
        def create(**kw):
            calls.append((name, kw))
            return body(kw)
        return N(chat=N(completions=N(create=create)))

    def call(primary, backup=None, **kw):
        if backup is not None:
            monkeypatch.setattr(engine, "get_client", lambda name: (client(name, backup), "small"))
        return engine._create_completion_with_fallback(
            [{"role": "user", "content": "hello"}], [], temperature=.1,
            system_prompt="audit", on_token=kw.pop("on_token", None),
            interrupt_check=lambda: False, trace=None, turn=1, max_tokens=8192,
            client_override=client("primary", primary), provider_override="primary",
            model_override="large", **kw)
    call.calls = calls
    return call


def fail(status=503):
    def body(kw):
        raise Failure(status, "primary failure")
    return body


def test_fallback_request_fits_its_own_completion_limit(api):
    assert api(fail(), lambda kw: response()).choices[0].message.content == "ok"
    assert api.calls[-1][1]["max_tokens"] == 128


def test_incompatible_fallback_is_skipped_before_an_api_request(api, engine, monkeypatch):
    engine.PROVIDERS["backup"]["context_window"] = "1"
    engine.PROVIDERS["next"] = {"model": "m", "context_window": "100000", "max_completion_tokens": "512"}
    monkeypatch.setitem(engine.APP_CONFIG, "fallback_models", "backup:small,next:m")
    api(fail(), lambda kw: response())
    assert [name for name, _ in api.calls] == ["primary", "next"]


def test_expired_budget_stops_before_fallback(api, engine, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(engine.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(engine, "_active_budget", engine._TurnBudget(max_seconds=10))

    def primary(kw):
        clock[0] = 11
        raise Failure(503, "timeout")

    with pytest.raises(RuntimeError, match="Turn budget exceeded"):
        api(primary, lambda kw: response())
    assert [name for name, _ in api.calls] == ["primary"]


def test_request_timeout_is_limited_to_remaining_time(api, engine, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(engine.time, "monotonic", lambda: clock[0])
    budget = engine._TurnBudget(max_seconds=10)
    clock[0] = 7
    monkeypatch.setattr(engine, "_active_budget", budget)
    api(lambda kw: response())
    assert 0 < api.calls[0][1]["timeout"] <= 3


def test_backoff_does_not_outlive_the_budget(api, engine, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(engine.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(engine.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    monkeypatch.setattr(engine, "_active_budget", engine._TurnBudget(max_seconds=1))
    monkeypatch.setattr(engine, "API_MAX_RETRIES", 2)
    monkeypatch.setattr(engine, "API_RETRY_INITIAL_DELAY_MS", 5000)
    monkeypatch.setattr(engine, "API_RETRY_JITTER_RATIO", 0)
    with pytest.raises(RuntimeError, match="Turn budget exceeded"):
        api(fail(), lambda kw: response())
    assert clock[0] <= 1.001
    assert len(api.calls) == 1


@pytest.mark.parametrize("status", [401, 403])
def test_bad_primary_credentials_try_an_independent_provider_once(api, status):
    api(fail(status), lambda kw: response())
    assert [name for name, _ in api.calls] == ["primary", "backup"]


def test_authentication_failure_skips_the_same_credentials(api, engine):
    engine.PROVIDERS["backup"]["api_key"] = "primary-key"
    with pytest.raises(Failure):
        api(fail(401), lambda kw: response())
    assert [name for name, _ in api.calls] == ["primary"]


def test_a_second_stream_failure_switches_provider_and_resets_partial_text(api):
    resets, tokens = [], []

    def stream(text, broken=False):
        yield N(choices=[N(delta=N(content=text, reasoning_content=None, tool_calls=None), finish_reason="stop")], usage=None)
        if broken:
            raise RuntimeError("connection reset")

    result = api(lambda kw: stream("partial", True), lambda kw: stream("complete"),
                 on_token=lambda kind, text: tokens.append(text),
                 on_stream_reset=lambda: resets.append(len(tokens)))
    assert result.choices[0].message.content == "complete"
    assert [name for name, _ in api.calls] == ["primary", "primary", "backup"]
    assert resets == [1, 2]


def test_fallback_configuration_is_normalized_deduplicated_and_diagnosed(engine, monkeypatch):
    monkeypatch.setitem(engine.APP_CONFIG, "fallback_models", " ollama : m ,missing:m,bad,ollama:,ollama:m")
    assert engine._fallback_targets() == [("ollama", "m")]
    warnings = "\n".join(engine.CONFIG_WARNINGS)
    assert "missing" in warnings and "bad" in warnings and "empty" in warnings
    assert "duplicate" in warnings
    monkeypatch.setitem(engine.APP_CONFIG, "fallback_models", "ollama:m")
    engine._fallback_targets()
    assert not any(w.startswith("fallback_models:") for w in engine.CONFIG_WARNINGS)


@pytest.mark.parametrize("tool,args", [
    ("browse_page", {"url": "https://example.com", "task": "submit the form"}),
    ("cli_anything_run", {"name": "app", "arguments": ["send", "message"]}),
])
def test_opaque_actions_are_not_replayed_even_after_an_intervening_read(engine, monkeypatch, tool, args):
    replies = iter([response(tool=(tool, args)), response(tool=("calculate", {"expression": "2+2"})),
                    response(tool=(tool, args)), response("done")])
    runs = []
    monkeypatch.setattr(engine, "create_completion", lambda *a, **k: next(replies))
    monkeypatch.setattr(engine, "exec_tool", lambda name, raw, **k: runs.append(name) or "accepted")
    monkeypatch.setattr(engine, "PERMISSION_MODE", "full-auto")
    engine.run_agent([{"role": "user", "content": "Use the browser and CLI to submit once."}],
                     allowed_tools={tool, "calculate"}, tools_def=[], max_turns=6)
    assert runs.count(tool) == 1


def test_stream_expiry_closes_the_stream_without_trying_a_fallback(api, engine, monkeypatch):
    clock, closed = [0.0], []
    monkeypatch.setattr(engine.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(engine, "_active_budget", engine._TurnBudget(max_seconds=10))

    class Stream:
        def __iter__(self):
            clock[0] = 11
            yield N(choices=[N(delta=N(content="partial", reasoning_content=None, tool_calls=None), finish_reason=None)], usage=None)

        def close(self):
            closed.append(True)

    with pytest.raises(RuntimeError, match="Turn budget exceeded"):
        api(lambda kw: Stream(), lambda kw: response(), on_token=lambda *a: None)
    assert closed
    assert len(api.calls) == 1


def test_late_response_does_not_execute_tools_and_reports_the_budget(engine, monkeypatch):
    clock, calls, answers = [0.0], [], []
    monkeypatch.setattr(engine.time, "monotonic", lambda: clock[0])
    budget = engine._TurnBudget(max_seconds=10)
    monkeypatch.setattr(engine, "PERMISSION_MODE", "full-auto")
    monkeypatch.setattr(engine, "MODEL_TELEMETRY_ENABLED", False)

    def create(**kw):
        clock[0] = 11
        return response(tool=("calculate", {"expression": "2+2"}))

    monkeypatch.setattr(engine, "client", N(chat=N(completions=N(create=create))))
    monkeypatch.setattr(engine, "exec_tool", lambda *a, **k: calls.append(1))
    result = engine.run_agent([{"role": "user", "content": "calculate 2+2"}], budget=budget,
                              max_turns=1, on_answer=answers.append, tools_def=[], allowed_tools={"calculate"})
    assert result.startswith("Turn budget exceeded")
    assert calls == [] and answers == [result]


def test_opaque_timeout_is_not_replayed(engine, monkeypatch):
    args = {"name": "app", "arguments": ["send", "message"]}
    replies = iter([response(tool=("cli_anything_run", args)),
                    response(tool=("cli_anything_run", args)), response("inspect state")])
    runs = []
    monkeypatch.setattr(engine, "create_completion", lambda *a, **k: next(replies))
    monkeypatch.setattr(engine, "exec_tool", lambda *a, **k: runs.append(1) or "Error: Command timed out; effects unknown.")
    monkeypatch.setattr(engine, "PERMISSION_MODE", "full-auto")
    engine.run_agent([{"role": "user", "content": "send it once"}],
                     allowed_tools={"cli_anything_run"}, tools_def=[], max_turns=4)
    assert runs == [1]


def test_known_observation_queries_can_refresh_without_buying_progress(engine, monkeypatch):
    runs = []
    monkeypatch.setattr(engine, "create_completion", lambda *a, **k: response(tool=("cli_anything_status", {})))
    monkeypatch.setattr(engine, "exec_tool", lambda *a, **k: runs.append(1) or "state")
    engine.run_agent([{"role": "user", "content": "check CLI status"}],
                     allowed_tools={"cli_anything_status"}, tools_def=[], max_turns=8)
    assert 1 < len(runs) <= 3
