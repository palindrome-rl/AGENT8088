"""provider.<name>.extra_body: a JSON object merged into every request's
extra_body, e.g. {"chat_template_kwargs": {"enable_thinking": false}} to switch
Qwen3 thinking off per request without touching the server's launch flags."""
import types


def _fake_client(seen):
    def create(**kwargs):
        seen.append(kwargs)
        message = types.SimpleNamespace(content="ok", tool_calls=None)
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message, finish_reason="stop")])
    return types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create)))


def _call(engine, provider="vllm", temperature=0.1):
    seen = []
    engine._create_completion(_fake_client(seen), [{"role": "user", "content": "hi"}], [],
                              system_prompt="sys", provider_name=provider, temperature=temperature)
    return seen[0]


def test_extra_body_json_is_sent(engine):
    engine.PROVIDERS["vllm"] = {"extra_body": '{"chat_template_kwargs": {"enable_thinking": false}}'}
    sent = _call(engine)
    assert sent["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}


def test_extra_body_merges_with_reasoning_effort(engine):
    engine.PROVIDERS["vllm"] = {"extra_body": '{"top_k": 20}', "reasoning_effort": "low"}
    sent = _call(engine)
    assert sent["extra_body"] == {"top_k": 20, "reasoning_effort": "low"}


def test_invalid_extra_body_is_ignored(engine):
    engine.PROVIDERS["vllm"] = {"extra_body": "{not json"}
    sent = _call(engine)
    assert "extra_body" not in sent


def test_no_extra_body_by_default(engine):
    engine.PROVIDERS["vllm"] = {}
    sent = _call(engine)
    assert "extra_body" not in sent


def test_provider_temperature_overrides_the_session_value(engine):
    engine.PROVIDERS["vllm"] = {"temperature": "0.6"}
    sent = _call(engine, temperature=0.1)
    assert sent["temperature"] == 0.6


def test_provider_temperature_is_ignored_by_default(engine):
    # A provider with no override keeps the value the caller passed in.
    engine.PROVIDERS["vllm"] = {}
    sent = _call(engine, temperature=0.1)
    assert sent["temperature"] == 0.1


def test_invalid_provider_temperature_falls_back_to_the_session_value(engine):
    engine.PROVIDERS["vllm"] = {"temperature": "not-a-number"}
    sent = _call(engine, temperature=0.1)
    assert sent["temperature"] == 0.1
