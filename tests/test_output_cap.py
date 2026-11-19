"""Output caps: the adaptive first-call cap, the write-retry ceiling, and the reasoning budget."""
import json
from types import SimpleNamespace as N

import pytest


def _usage(tokens):
    return N(completion_tokens=tokens, prompt_tokens=10, total_tokens=tokens + 10)


# --- the adaptive first-call cap ---------------------------------------------------------

@pytest.mark.parametrize("recent,expected", [
    ([], 8192),                       # no samples: the ceiling stands
    ([100, 200], 8192),               # fewer than three
    ([100, 200, 300], 4096),          # small replies settle at the floor
    ([100, 200, 300, 250, 150], 4096),
    ([4500, 4600, 4700], 8192),       # 2 x median clears the ceiling
    ([2500, 2600, 2700], 5200),       # in between: 2 x median
    ([100, 100, 100, 100, 7000], 4096),   # one large reply does not move the median
])
def test_adaptive_first_cap(engine, recent, expected):
    assert engine._adaptive_first_cap(recent, 8192, 4096) == expected


def test_a_ceiling_below_the_floor_wins(engine):
    assert engine._adaptive_first_cap([10, 10, 10], 2048, 4096) == 2048


def test_the_feature_can_be_turned_off(engine, monkeypatch):
    monkeypatch.setattr(engine, "ADAPTIVE_COMPLETION_CAP", False)
    assert engine._adaptive_first_cap([10, 10, 10], 8192, 4096) == 8192


class Script:
    def __init__(self, engine, monkeypatch, replies):
        self.caps, self.replies = [], list(replies)

        def fake(messages, tools, max_tokens=None, **kw):
            self.caps.append(max_tokens)
            index = min(len(self.caps) - 1, len(self.replies) - 1)
            return self.replies[index]

        monkeypatch.setattr(engine, "_create_completion_with_fallback", fake)
        monkeypatch.setattr(engine, "_active_model_token_limits", lambda *a, **k: (131072, 32768))
        monkeypatch.setattr(engine, "INITIAL_COMPLETION_CAP", 8192)
        # these tests are about the retry mechanics at the old fixed caps (8192 / 4096 / 4096); the scaled defaults have their own tests
        for flag in ("_INITIAL_CAP_SET", "_MIN_TOKENS_SET", "_THINKING_OFF_SET"):
            monkeypatch.setattr(engine, flag, True)
        monkeypatch.setattr(engine, "exec_tool", lambda name, arguments, depth=0: "ok")


def _reply(text, tokens=None, finish="stop", reasoning=""):
    message = N(content=text, tool_calls=None, reasoning_content=reasoning)
    return N(choices=[N(message=message, finish_reason=finish)],
             usage=_usage(tokens) if tokens is not None else None)


def _tool_reply(n, tokens=None, reasoning=""):
    return _reply(f'✿FUNCTION✿: execute_shell ✿ARGS✿: {json.dumps({"command": f"echo {n}"})}', tokens,
                  reasoning=reasoning)


def run(engine, replies, monkeypatch, max_turns=12):
    script = Script(engine, monkeypatch, replies)
    engine.run_agent([{"role": "user", "content": "work through it"}], max_turns=max_turns,
                     tools_def=[], allowed_tools={"execute_shell"})
    return script.caps


def test_the_cap_settles_at_the_floor_after_a_few_short_replies(engine, monkeypatch):
    caps = run(engine, [_tool_reply(n, 120) for n in range(5)] + [_reply("done", 50)], monkeypatch)
    assert caps[:3] == [8192, 8192, 8192]
    assert caps[3:6] == [4096, 4096, 4096]


def test_big_replies_keep_the_cap_where_it_was(engine, monkeypatch):
    caps = run(engine, [_tool_reply(n, 5000) for n in range(5)] + [_reply("done", 50)], monkeypatch)
    assert set(caps) == {8192}


def test_without_usage_the_reasoning_counts_toward_the_size(engine, monkeypatch):
    """Streamed replies carry no usage. A model that thinks at length must not look terse."""
    long_thought = "t" * 30000   # ~7500 tokens of reasoning
    caps = run(engine, [_tool_reply(n, None, reasoning=long_thought) for n in range(5)] + [_reply("done", 5)],
               monkeypatch)
    assert set(caps) == {8192}


def test_the_feature_off_keeps_the_fixed_cap(engine, monkeypatch):
    monkeypatch.setattr(engine, "ADAPTIVE_COMPLETION_CAP", False)
    caps = run(engine, [_tool_reply(n, 120) for n in range(5)] + [_reply("done", 50)], monkeypatch)
    assert set(caps) == {8192}


def test_a_zero_initial_cap_disables_the_adaptive_cap_too(engine, monkeypatch):
    script = Script(engine, monkeypatch, [_tool_reply(n, 120) for n in range(5)] + [_reply("done", 50)])
    monkeypatch.setattr(engine, "INITIAL_COMPLETION_CAP", 0)
    engine.run_agent([{"role": "user", "content": "go"}], max_turns=12,
                     tools_def=[], allowed_tools={"execute_shell"})
    assert set(script.caps) == {32768}


def test_a_big_write_after_short_replies_recovers_through_the_retry_cap(engine, monkeypatch):
    partial = '✿FUNCTION✿: write_file ✿ARGS✿: {"filename": "a", "content": "xxxx'
    replies = [_tool_reply(n, 120) for n in range(4)] + [_reply(partial, finish="length")] + [_reply("done", 50)]
    caps = run(engine, replies, monkeypatch)
    # Three short replies, then the floor; the cut-off tool call doubles from the floor.
    assert caps[3] == 4096 and caps[4] == 4096
    assert caps[5] == 8192


# --- the write-retry ceiling -------------------------------------------------------------------

@pytest.mark.parametrize("failed,limit,write_cap,expected", [
    (4096, 100000, 16384, 8192),
    (8192, 100000, 16384, 16384),
    (16384, 100000, 16384, 16384),      # plateau at the write cap
    (16384, 100000, 0, 32768),          # no write cap: plain doubling
    (8192, 12000, 16384, 12000),        # the provider's limit still wins
    (20000, 100000, 16384, 20000),      # never lower than the call that failed
])
def test_write_retry_cap(engine, monkeypatch, failed, limit, write_cap, expected):
    monkeypatch.setattr(engine, "WRITE_OUTPUT_CAP", write_cap)
    assert engine._write_retry_cap(failed, limit) == expected


# --- the reasoning budget ---------------------------------------------------------------------

def _providers(engine, monkeypatch, **profiles):
    monkeypatch.setattr(engine, "PROVIDERS", profiles)


def test_no_budget_by_default(engine, monkeypatch):
    _providers(engine, monkeypatch, openrouter={"base_url": "https://openrouter.ai/api/v1"})
    assert engine.REASONING_BUDGET_TOKENS == 0
    assert engine._reasoning_budget_extra_body("openrouter") == {}


def test_openrouter_gets_its_documented_field(engine, monkeypatch):
    monkeypatch.setattr(engine, "REASONING_BUDGET_TOKENS", 3000)
    _providers(engine, monkeypatch, openrouter={"base_url": "https://openrouter.ai/api/v1"},
               custom={"base_url": "https://x.example/v1"})
    assert engine._reasoning_budget_extra_body("openrouter") == {"reasoning": {"max_tokens": 3000}}
    assert engine._reasoning_budget_extra_body("custom") == {}, "an unknown provider is left alone"
    assert engine._reasoning_budget_extra_body("") == {}


def test_a_provider_template_is_filled_in(engine, monkeypatch):
    monkeypatch.setattr(engine, "REASONING_BUDGET_TOKENS", 2048)
    _providers(engine, monkeypatch, vllm={
        "reasoning_budget_extra_body": '{"chat_template_kwargs": {"thinking_budget": {budget}}}'})
    assert engine._reasoning_budget_extra_body("vllm") == {"chat_template_kwargs": {"thinking_budget": 2048}}


def test_a_broken_template_is_ignored(engine, monkeypatch):
    monkeypatch.setattr(engine, "REASONING_BUDGET_TOKENS", 2048)
    _providers(engine, monkeypatch, vllm={"reasoning_budget_extra_body": "not json {budget}"},
               other={"reasoning_budget_extra_body": "[1, 2]"})
    assert engine._reasoning_budget_extra_body("vllm") == {}
    assert engine._reasoning_budget_extra_body("other") == {}


def test_a_provider_can_set_or_switch_off_its_own_budget(engine, monkeypatch):
    monkeypatch.setattr(engine, "REASONING_BUDGET_TOKENS", 3000)
    _providers(engine, monkeypatch,
               a={"base_url": "https://openrouter.ai/api/v1", "reasoning_budget_tokens": "1500"},
               b={"base_url": "https://openrouter.ai/api/v1", "reasoning_budget_tokens": "0"})
    assert engine._reasoning_budget_extra_body("a") == {"reasoning": {"max_tokens": 1500}}
    assert engine._reasoning_budget_extra_body("b") == {}


class _Client:
    def __init__(self):
        self.seen = {}
        outer = self

        class _Completions:
            def create(self, **kw):
                outer.seen.update(kw)
                return _reply("ok", 5)

        self.chat = N(completions=_Completions())


def _send(engine, thinking=None, provider="openrouter"):
    client = _Client()
    engine._create_completion(client, [{"role": "user", "content": "hi"}], [], model_name="m",
                              provider_name=provider, thinking=thinking)
    return client.seen


def test_an_ordinary_call_carries_the_budget(engine, monkeypatch):
    monkeypatch.setattr(engine, "REASONING_BUDGET_TOKENS", 3000)
    _providers(engine, monkeypatch, openrouter={"base_url": "https://openrouter.ai/api/v1"})
    assert _send(engine)["extra_body"] == {"reasoning": {"max_tokens": 3000}}


def test_the_providers_own_extra_body_wins_on_a_clash(engine, monkeypatch):
    monkeypatch.setattr(engine, "REASONING_BUDGET_TOKENS", 3000)
    _providers(engine, monkeypatch, openrouter={
        "base_url": "https://openrouter.ai/api/v1",
        "extra_body": '{"reasoning": {"effort": "low"}, "provider": {"order": ["x"]}}'})
    assert _send(engine)["extra_body"] == {"reasoning": {"effort": "low"}, "provider": {"order": ["x"]}}


def test_retries_with_thinking_changed_do_not_carry_the_budget(engine, monkeypatch):
    monkeypatch.setattr(engine, "REASONING_BUDGET_TOKENS", 3000)
    _providers(engine, monkeypatch, openrouter={"base_url": "https://openrouter.ai/api/v1"})
    assert _send(engine, thinking="length_retry")["extra_body"] == {"reasoning_effort": "none"}
    assert "reasoning" not in _send(engine, thinking=False).get("extra_body", {})


def test_no_budget_means_no_extra_body_at_all(engine, monkeypatch):
    _providers(engine, monkeypatch, openrouter={"base_url": "https://openrouter.ai/api/v1"})
    assert "extra_body" not in _send(engine)


@pytest.mark.parametrize("message,rejected", [
    ("Unknown parameter: 'reasoning.max_tokens'", True),
    ("Unsupported field: reasoning", True),
    ("The model returned an internal error", False),
])
def test_a_rejected_budget_field_is_retried_clean(engine, message, rejected):
    exc = type("E", (Exception,), {"status_code": 400})(message)
    assert engine._extras_rejected(exc, None) is rejected
