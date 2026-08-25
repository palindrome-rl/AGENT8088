"""Agent8088ChatModel lets browser-use's Agent loop reuse agent8088's own
already-configured provider (no second LLM credential path) and charges
every call to the caller's existing turn budget, so a browsing task can't
spend tokens the user's budget ceiling doesn't know about."""
import pytest

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
