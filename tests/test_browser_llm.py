"""Agent8088ChatModel lets browser-use's Agent loop reuse agent8088's own
already-configured provider (no second LLM credential path) and charges
every call to the caller's existing turn budget, so a browsing task can't
spend tokens the user's budget ceiling doesn't know about."""
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from agent8088.browser_llm import Agent8088ChatModel, build_browser_chat_model


class _FakeUsage:
    def __init__(self, prompt_tokens, completion_tokens):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = prompt_tokens + completion_tokens


class _FakeCompletion:
    def __init__(self, usage):
        self.completion = "done"
        self.usage = usage


class _FakeBudget:
    def __init__(self, exceeded_reason=None):
        self._exceeded_reason = exceeded_reason
        self.charged = []

    def exceeded(self):
        return self._exceeded_reason

    def add_tokens(self, prompt, completion):
        self.charged.append((prompt, completion))


def test_build_browser_chat_model_from_litellm_style_client():
    client = {"api_mode": "litellm", "api_base": "https://api.example.com", "api_key": "sk-x"}
    model = build_browser_chat_model(client, "openai/gpt-4o", budget=None)
    assert isinstance(model, Agent8088ChatModel)
    assert model.model == "openai/gpt-4o"
    assert model.api_base == "https://api.example.com"
    assert model.api_key == "sk-x"


def test_build_browser_chat_model_from_openai_sdk_style_client():
    class _FakeSDKClient:
        api_key = "sk-y"
        base_url = "http://localhost:11434/v1"

    model = build_browser_chat_model(_FakeSDKClient(), "llama3", budget=None)
    assert model.model == "openai/llama3"
    assert model.api_base == "http://localhost:11434/v1"
    assert model.api_key == "sk-y"


def test_max_tokens_defaults_to_chatlitellms_own_value_when_not_given():
    client = {"api_mode": "litellm", "api_base": "https://api.example.com", "api_key": "sk-x"}
    model = build_browser_chat_model(client, "openai/gpt-4o", budget=None)
    assert model.max_tokens == 4096  # ChatLiteLLM's own default


def test_max_tokens_is_threaded_through_for_both_client_shapes():
    litellm_client = {"api_mode": "litellm", "api_base": "https://api.example.com", "api_key": "sk-x"}
    model = build_browser_chat_model(litellm_client, "openai/gpt-4o", budget=None, max_tokens=8192)
    assert model.max_tokens == 8192

    class _FakeSDKClient:
        api_key = "sk-y"
        base_url = "http://localhost:11434/v1"

    model = build_browser_chat_model(_FakeSDKClient(), "llama3", budget=None, max_tokens=8192)
    assert model.max_tokens == 8192


@pytest.mark.asyncio
async def test_ainvoke_charges_the_budget_on_success(monkeypatch):
    budget = _FakeBudget()
    model = Agent8088ChatModel(model="openai/gpt-4o", budget=budget)

    async def fake_super_ainvoke(self, messages, output_format=None, **kwargs):
        return _FakeCompletion(_FakeUsage(prompt_tokens=100, completion_tokens=20))

    monkeypatch.setattr(
        "browser_use.llm.litellm.ChatLiteLLM.ainvoke", fake_super_ainvoke, raising=True)

    result = await model.ainvoke([])

    assert result.completion == "done"
    assert budget.charged == [(100, 20)]


@pytest.mark.asyncio
async def test_ainvoke_refuses_when_budget_already_exceeded():
    budget = _FakeBudget(exceeded_reason="Turn budget exceeded: 9999 tokens used (limit 1000).")
    model = Agent8088ChatModel(model="openai/gpt-4o", budget=budget)

    with pytest.raises(RuntimeError, match="Turn budget exceeded"):
        await model.ainvoke([])

    assert budget.charged == []


@pytest.mark.asyncio
async def test_ainvoke_without_a_budget_still_works(monkeypatch):
    model = Agent8088ChatModel(model="openai/gpt-4o", budget=None)

    async def fake_super_ainvoke(self, messages, output_format=None, **kwargs):
        return _FakeCompletion(_FakeUsage(prompt_tokens=5, completion_tokens=5))

    monkeypatch.setattr(
        "browser_use.llm.litellm.ChatLiteLLM.ainvoke", fake_super_ainvoke, raising=True)

    result = await model.ainvoke([])

    assert result.completion == "done"


class _Output(BaseModel):
    thinking: str
    answer: str


@pytest.mark.asyncio
async def test_ainvoke_falls_back_to_json_object_mode_when_strict_schema_is_ignored(
        monkeypatch):
    """Some providers (observed: Ollama Cloud serving glm-5.2) accept a
    response_format=json_schema request without error but simply ignore it
    and return plain prose - browser-use's own ChatLiteLLM has no fallback
    for this, so output_format.model_validate_json(content) raises a
    pydantic ValidationError that propagates straight out of ainvoke().
    browser-use's Agent retries the exact same request up to max_retries
    times, which fails identically every time since the provider's behavior
    never changes, burning the whole step. The fix has to happen here, one
    layer below browser-use's retry loop, by falling back to the far more
    widely-supported json_object mode plus an explicit schema instruction -
    verified directly against Ollama Cloud to actually produce valid JSON
    where json_schema mode did not."""
    from pydantic import ValidationError

    model = Agent8088ChatModel(model="openai/glm-5.2", budget=None)

    async def fake_super_ainvoke(self, messages, output_format=None, **kwargs):
        try:
            output_format.model_validate_json("The sky is blue today.")
        except ValidationError as exc:
            raise exc from None

    monkeypatch.setattr(
        "browser_use.llm.litellm.ChatLiteLLM.ainvoke", fake_super_ainvoke, raising=True)

    calls = []

    async def fake_acompletion(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(
                content='{"thinking": "ok", "answer": "The sky is blue."}'))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        )

    monkeypatch.setattr("litellm.acompletion", fake_acompletion)

    result = await model.ainvoke([], output_format=_Output)

    assert result.completion == _Output(thinking="ok", answer="The sky is blue.")
    assert calls[0]["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_json_object_fallback_charges_the_budget(monkeypatch):
    budget = _FakeBudget()
    model = Agent8088ChatModel(model="openai/glm-5.2", budget=budget)

    async def fake_super_ainvoke(self, messages, output_format=None, **kwargs):
        from pydantic import ValidationError
        try:
            output_format.model_validate_json("not json")
        except ValidationError as exc:
            raise exc from None

    monkeypatch.setattr(
        "browser_use.llm.litellm.ChatLiteLLM.ainvoke", fake_super_ainvoke, raising=True)

    async def fake_acompletion(**kwargs):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(
                content='{"thinking": "ok", "answer": "fine"}'))],
            usage=SimpleNamespace(prompt_tokens=7, completion_tokens=3),
        )

    monkeypatch.setattr("litellm.acompletion", fake_acompletion)

    await model.ainvoke([], output_format=_Output)

    assert budget.charged == [(7, 3)]


@pytest.mark.asyncio
async def test_a_genuinely_unparseable_fallback_response_still_raises(monkeypatch):
    """The fallback is a best-effort recovery, not a guarantee - if the
    provider ignores json_object mode too, the caller must still see a
    failure rather than a silently wrong/empty result."""
    model = Agent8088ChatModel(model="openai/glm-5.2", budget=None)

    async def fake_super_ainvoke(self, messages, output_format=None, **kwargs):
        from pydantic import ValidationError
        try:
            output_format.model_validate_json("nope")
        except ValidationError as exc:
            raise exc from None

    monkeypatch.setattr(
        "browser_use.llm.litellm.ChatLiteLLM.ainvoke", fake_super_ainvoke, raising=True)

    async def fake_acompletion(**kwargs):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(
                content="still not json"))],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
        )

    monkeypatch.setattr("litellm.acompletion", fake_acompletion)

    with pytest.raises(Exception):
        await model.ainvoke([], output_format=_Output)


@pytest.mark.asyncio
async def test_non_structured_calls_are_unaffected_by_the_fallback(monkeypatch):
    """output_format=None means no schema was requested in the first place -
    the fallback must never trigger for a plain text completion."""
    model = Agent8088ChatModel(model="openai/glm-5.2", budget=None)

    async def fake_super_ainvoke(self, messages, output_format=None, **kwargs):
        return _FakeCompletion(_FakeUsage(prompt_tokens=2, completion_tokens=2))

    monkeypatch.setattr(
        "browser_use.llm.litellm.ChatLiteLLM.ainvoke", fake_super_ainvoke, raising=True)

    async def fake_acompletion(**kwargs):
        raise AssertionError("fallback must not run for a plain-text call")

    monkeypatch.setattr("litellm.acompletion", fake_acompletion)

    result = await model.ainvoke([])

    assert result.completion == "done"
