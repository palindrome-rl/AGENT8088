"""Agent8088ChatModel lets browser-use's Agent loop reuse agent8088's own
already-configured provider (no second LLM credential path) and charges
every call to the caller's existing turn budget, so a browsing task can't
spend tokens the user's budget ceiling doesn't know about."""
import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ValidationError

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
async def test_extra_body_is_sent_on_structured_browser_calls(monkeypatch):
    model = Agent8088ChatModel(
        model="openai/qwen", extra_body={"chat_template_kwargs": {"enable_thinking": False}})
    calls = []

    async def fake_acompletion(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"thinking": "", "answer": "ok"}'),
                                     finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
        )

    monkeypatch.setattr("litellm.acompletion", fake_acompletion)

    result = await model.ainvoke([], output_format=_Output)

    assert result.completion.answer == "ok"
    assert calls[0]["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}


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


class _Action(BaseModel):
    wait: int


class _ActionOutput(BaseModel):
    action: list[_Action]


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
                content='`json\n{"thinking": "ok", "answer": "The sky is blue."}\n`'))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        )

    monkeypatch.setattr("litellm.acompletion", fake_acompletion)

    result = await model.ainvoke([], output_format=_Output)

    assert result.completion == _Output(thinking="ok", answer="The sky is blue.")
    assert calls[0]["response_format"] == {"type": "json_object"}
    assert calls[0]["num_retries"] == model.max_retries


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
async def test_json_object_fallback_turns_empty_browser_actions_into_a_retryable_list(monkeypatch):
    model = Agent8088ChatModel(model="openai/test", budget=None)

    async def fake_super_ainvoke(self, messages, output_format=None, **kwargs):
        try:
            output_format.model_validate_json('{"action": [{}]}')
        except ValidationError as exc:
            raise exc from None

    monkeypatch.setattr(
        "browser_use.llm.litellm.ChatLiteLLM.ainvoke", fake_super_ainvoke, raising=True)

    async def fake_acompletion(**kwargs):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"action": [{}]}'))],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
        )

    monkeypatch.setattr("litellm.acompletion", fake_acompletion)

    result = await model.ainvoke([], output_format=_ActionOutput)

    assert result.completion.action == []


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


# --- the schema instruction must not displace the system message -------------
# The json_object fallback appended its schema instruction as a *trailing*
# system message. Several OpenAI-compatible servers reject that outright with
# "System message must be at the beginning" - observed on a llama.cpp/llama-swap
# box serving Qwen3.8-27B, and on Ollama Cloud serving GLM. Every fallback
# attempt then failed with a BadRequestError and browser-use retried it 6
# times, so the fallback was broken for the very provider class it exists for.
# Keeping any system message at index 0 is accepted everywhere, so that is the
# invariant these tests pin - not one vendor's wording.

def _fallback_probe(monkeypatch, messages):
    """Run the json_object fallback and return the messages it sent."""
    from pydantic import ValidationError

    model = Agent8088ChatModel(model="openai/glm-5.3-flash", budget=None)

    async def fake_super_ainvoke(self, msgs, output_format=None, **kwargs):
        try:
            output_format.model_validate_json("not json at all")
        except ValidationError as exc:
            raise exc from None

    monkeypatch.setattr(
        "browser_use.llm.litellm.ChatLiteLLM.ainvoke", fake_super_ainvoke, raising=True)
    monkeypatch.setattr(
        "agent8088.browser_llm.LiteLLMMessageSerializer.serialize",
        staticmethod(lambda _m: messages), raising=True)

    calls = []

    async def fake_acompletion(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(
                content='{"thinking": "ok", "answer": "done"}'))],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
        )

    monkeypatch.setattr("litellm.acompletion", fake_acompletion)
    return model, calls


@pytest.mark.asyncio
async def test_no_system_message_appears_after_the_first_position(monkeypatch):
    model, calls = _fallback_probe(monkeypatch, [
        {"role": "system", "content": "You are a browser agent."},
        {"role": "user", "content": "current state"},
    ])

    await model.ainvoke([], output_format=_Output)

    sent = calls[0]["messages"]
    later_roles = [m["role"] for m in sent[1:]]
    assert "system" not in later_roles, f"system message not at the front: {later_roles}"


@pytest.mark.asyncio
async def test_the_schema_instruction_still_reaches_the_model(monkeypatch):
    model, calls = _fallback_probe(monkeypatch, [
        {"role": "system", "content": "You are a browser agent."},
        {"role": "user", "content": "current state"},
    ])

    await model.ainvoke([], output_format=_Output)

    blob = json.dumps(calls[0]["messages"])
    assert "single JSON object" in blob
    # and the original system text is not thrown away
    assert "You are a browser agent." in blob


@pytest.mark.asyncio
async def test_a_leading_system_message_is_preserved_not_replaced(monkeypatch):
    model, calls = _fallback_probe(monkeypatch, [
        {"role": "system", "content": "ORIGINAL RULES"},
        {"role": "user", "content": "state"},
    ])

    await model.ainvoke([], output_format=_Output)

    head = calls[0]["messages"][0]
    assert head["role"] == "system"
    assert "ORIGINAL RULES" in json.dumps(head["content"])


@pytest.mark.asyncio
async def test_a_conversation_with_no_system_message_gets_one_at_the_front(monkeypatch):
    model, calls = _fallback_probe(monkeypatch, [
        {"role": "user", "content": "state"},
    ])

    await model.ainvoke([], output_format=_Output)

    sent = calls[0]["messages"]
    assert sent[0]["role"] == "system"
    assert "single JSON object" in json.dumps(sent[0]["content"])
    assert [m["role"] for m in sent[1:]] == ["user"]


@pytest.mark.asyncio
async def test_multimodal_system_content_is_handled_without_crashing(monkeypatch):
    """browser-use can serialize content as a list of parts, not a bare string."""
    model, calls = _fallback_probe(monkeypatch, [
        {"role": "system", "content": [{"type": "text", "text": "RULES"}]},
        {"role": "user", "content": "state"},
    ])

    await model.ainvoke([], output_format=_Output)

    sent = calls[0]["messages"]
    assert [m["role"] for m in sent] == ["system", "user"]
    blob = json.dumps(sent[0]["content"])
    assert "RULES" in blob and "single JSON object" in blob


@pytest.mark.asyncio
async def test_the_callers_message_list_is_not_mutated(monkeypatch):
    original = [
        {"role": "system", "content": "RULES"},
        {"role": "user", "content": "state"},
    ]
    model, calls = _fallback_probe(monkeypatch, original)

    await model.ainvoke([], output_format=_Output)

    assert original == [
        {"role": "system", "content": "RULES"},
        {"role": "user", "content": "state"},
    ]


# ---------------------------------------------------------------------------
# Salvaging a JSON object out of a reasoning model's prose
# ---------------------------------------------------------------------------
# Captured verbatim from Ollama Cloud serving glm-5.2 during a real browse_page
# run: the model obeys "reply with JSON" but prefixes a sentence of narration,
# or wraps the object in a fence *after* that narration, or emits a second
# object after the first. json.loads() rejects all three with the errors seen
# in the live logs ("Expecting value: line 1 column 1 (char 0)" and "Extra
# data: line 1 column 27"). browser-use's Agent counts each as a failed step
# and retries the identical request up to 6 times - so one unparsed byte costs
# a whole step's worth of latency and tokens. Measured on four live runs:
# 2-8 wasted LLM round trips per browse_page call.

_PROSE_THEN_OBJECT = (
    "I have all the information needed from the browser state.\n\n"
    '{"thinking": "ok", "answer": "The sky is blue."}'
)
_PROSE_THEN_FENCED_OBJECT = (
    "I can see the first three books listed on the page.\n\n"
    "I have all the information needed to complete the task.```json\n"
    '{"thinking": "ok", "answer": "The sky is blue."}\n```'
)
_OBJECT_THEN_EXTRA_DATA = (
    '{"thinking": "ok", "answer": "The sky is blue."}'
    '{"thinking": "again", "answer": "duplicate"}'
)


@pytest.mark.parametrize("content", [
    _PROSE_THEN_OBJECT,
    _PROSE_THEN_FENCED_OBJECT,
    _OBJECT_THEN_EXTRA_DATA,
])
def test_a_json_object_buried_in_prose_is_recovered_not_rejected(content):
    from agent8088.browser_llm import _parse_structured_output

    assert _parse_structured_output(_Output, content) == _Output(
        thinking="ok", answer="The sky is blue.")


def test_prose_with_no_json_object_at_all_still_raises():
    """Salvage must not paper over a provider that returned no object: that is
    a genuine failure the caller has to see, not something to invent a value
    for."""
    from agent8088.browser_llm import _parse_structured_output

    with pytest.raises(Exception):
        _parse_structured_output(_Output, "I cannot help with that request.")


def test_a_brace_inside_a_string_does_not_end_the_object_early():
    """The scan has to respect JSON string quoting - a '}' inside a value is
    not the end of the object."""
    from agent8088.browser_llm import _parse_structured_output

    content = ('Here you go:\n'
               '{"thinking": "a } brace and a \\" quote", "answer": "done"}')
    assert _parse_structured_output(_Output, content) == _Output(
        thinking='a } brace and a " quote', answer="done")


@pytest.mark.asyncio
async def test_empty_content_for_a_structured_request_also_falls_back(monkeypatch):
    """A reasoning model can spend its whole completion budget on
    reasoning_content and return content="". browser-use's ChatLiteLLM raises
    ModelProviderError (not ValidationError) for that case, so the
    json_object fallback never fired - the step failed and was retried
    identically, exactly the pathology the fallback exists to prevent."""
    from browser_use.llm.exceptions import ModelProviderError

    model = Agent8088ChatModel(model="openai/glm-5.2", budget=None)

    async def fake_super_ainvoke(self, messages, output_format=None, **kwargs):
        raise ModelProviderError(
            message="Model returned empty content for structured output request",
            status_code=500, model="openai/glm-5.2")

    monkeypatch.setattr(
        "browser_use.llm.litellm.ChatLiteLLM.ainvoke", fake_super_ainvoke, raising=True)

    async def fake_acompletion(**kwargs):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(
                content='{"thinking": "ok", "answer": "recovered"}'))],
            usage=SimpleNamespace(prompt_tokens=4, completion_tokens=2),
        )

    monkeypatch.setattr("litellm.acompletion", fake_acompletion)

    result = await model.ainvoke([], output_format=_Output)

    assert result.completion == _Output(thinking="ok", answer="recovered")


def test_a_valid_top_level_json_array_is_not_reduced_to_its_first_object():
    """Salvage must be a fallback, never a pre-filter. Scanning for the first
    balanced {...} before attempting a straight parse would silently turn a
    perfectly valid top-level array into just its first element - a
    correctness regression introduced by the recovery path itself."""
    from pydantic import RootModel

    from agent8088.browser_llm import _parse_structured_output

    class _Items(RootModel[list[dict]]):
        pass

    parsed = _parse_structured_output(_Items, '[{"a": 1}, {"b": 2}]')

    assert parsed.root == [{"a": 1}, {"b": 2}]
