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


def _call(engine, provider="vllm"):
    seen = []
    engine._create_completion(_fake_client(seen), [{"role": "user", "content": "hi"}], [],
                              system_prompt="sys", provider_name=provider)
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
