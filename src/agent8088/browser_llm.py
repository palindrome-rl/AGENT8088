"""Bridges browser-use's Agent to agent8088's own already-configured LLM
provider, instead of wiring a second, independent LLM credential path.

Agent8088ChatModel subclasses browser-use's own ChatLiteLLM
(browser_use.llm.litellm.ChatLiteLLM) and adds one thing: every call is
charged against a caller-supplied budget object (engine._TurnBudget, passed
in by _exec_browser as _active_budget) using the exact same add_tokens()
call run_agent()'s own loop uses - so a multi-step browsing task can't spend
tokens outside the user's existing turn budget ceiling.
"""
from dataclasses import dataclass
import json
from typing import Any, Optional

from pydantic import ValidationError
from browser_use.llm.litellm import ChatLiteLLM
from browser_use.llm.litellm.serializer import LiteLLMMessageSerializer
from browser_use.llm.schema import SchemaOptimizer
from browser_use.llm.views import ChatInvokeCompletion


def _unfence_json_object(content: str) -> str:
    """Return JSON wrapped in a Markdown fence as plain JSON."""
    original = content
    content = content.strip()
    fence_size = len(content) - len(content.lstrip("`"))
    if not 1 <= fence_size <= 3 or not content.endswith("`" * fence_size):
        return original
    content = content[fence_size:-fence_size].lstrip()
    if content[:4].lower() == "json":
        content = content[4:].lstrip()
    return content if content.startswith("{") else original


def _parse_structured_output(output_format, content: str):
    content = _unfence_json_object(content)
    try:
        return output_format.model_validate_json(content)
    except ValidationError:
        try:
            value = json.loads(content)
        except json.JSONDecodeError:
            raise
        if "action" not in getattr(output_format, "model_fields", {}) or not isinstance(value, dict):
            raise
        actions = value.get("action")
        if actions is not None and (not isinstance(actions, list)
                                    or any(action for action in actions)):
            raise
        value["action"] = []
        return output_format.model_validate(value)


@dataclass
class Agent8088ChatModel(ChatLiteLLM):
    budget: Optional[Any] = None  # duck-typed engine._TurnBudget: .exceeded() / .add_tokens()

    async def ainvoke(self, messages, output_format=None, **kwargs):
        if self.budget is not None:
            over = self.budget.exceeded()
            if over:
                raise RuntimeError(over)
        try:
            result = await super().ainvoke(messages, output_format, **kwargs)
        except ValidationError:
            # Some OpenAI-compatible providers (observed: Ollama Cloud
            # serving glm-5.2) accept response_format=json_schema without
            # error but silently ignore it and return plain prose. browser-
            # use's ChatLiteLLM has no fallback for that - the resulting
            # ValidationError from output_format.model_validate_json(content)
            # propagates straight out, and browser-use's own Agent retries
            # the identical request up to max_retries times, which fails the
            # same way every time since the provider's behavior never
            # changes. json_object mode is far more widely supported;
            # retrying with it (plus an explicit schema instruction) is a
            # one-shot recovery, not a second full retry loop.
            if output_format is None:
                raise
            result = await self._ainvoke_json_object_fallback(
                messages, output_format, **kwargs)
        if self.budget is not None and result.usage is not None:
            self.budget.add_tokens(result.usage.prompt_tokens, result.usage.completion_tokens)
        return result

    async def _ainvoke_json_object_fallback(self, messages, output_format, **kwargs):
        from litellm import acompletion

        schema = SchemaOptimizer.create_optimized_json_schema(output_format)
        litellm_messages = LiteLLMMessageSerializer.serialize(messages)
        litellm_messages = litellm_messages + [{
            "role": "system",
            "content": (
                "Respond with ONLY a single JSON object matching this schema, "
                f"and no other text:\n{schema}"
            ),
        }]

        params: dict = {
            "model": self.model,
            "messages": litellm_messages,
            "response_format": {"type": "json_object"},
            "num_retries": self.max_retries,
        }
        if self.temperature is not None:
            params["temperature"] = self.temperature
        if self.max_tokens is not None:
            params["max_tokens"] = self.max_tokens
        if self.api_key:
            params["api_key"] = self.api_key
        if self.api_base:
            params["api_base"] = self.api_base

        response = await acompletion(**params)
        content = response.choices[0].message.content or ""
        parsed = _parse_structured_output(output_format, content)
        return ChatInvokeCompletion(
            completion=parsed,
            usage=self._parse_usage(response),
        )


def build_browser_chat_model(
    client, model_name: str, budget=None, max_tokens: Optional[int] = None
) -> Agent8088ChatModel:
    """Build a browser-use chat model that targets the exact same
    provider/model engine.py's main loop is already configured for.

    `client` is engine.py's module-level `client` global: either a litellm-
    mode dict ({"api_mode": "litellm", "api_base": ..., "api_key": ...}) or
    an OpenAI-SDK-style object (has .base_url / .api_key attributes) for
    non-litellm provider configs. Both are normalized into a litellm model
    string here, since ChatLiteLLM always calls litellm under the hood -
    an OpenAI-SDK-style client's base_url/api_key describe an
    OpenAI-compatible endpoint, which litellm can also reach via the
    `openai/<model>` provider prefix plus a custom api_base.

    `max_tokens` should be the caller's own completion-token ceiling
    (engine.py's MAX_COMPLETION_TOKENS): left at ChatLiteLLM's own default
    of 4096, a model that spends much of its budget on the "thinking" field
    before writing the actual action can get cut off mid-response, which
    browser-use reports as "Model returned empty action" and retries the
    whole step - a silent, avoidable source of wasted round-trips."""
    kwargs = {"budget": budget}
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    if isinstance(client, dict) and client.get("api_mode") == "litellm":
        return Agent8088ChatModel(
            model=model_name,
            api_key=client.get("api_key") or None,
            api_base=client.get("api_base") or None,
            **kwargs,
        )
    api_key = getattr(client, "api_key", None)
    base_url = getattr(client, "base_url", None)
    return Agent8088ChatModel(
        model=f"openai/{model_name}",
        api_key=api_key,
        api_base=str(base_url) if base_url else None,
        **kwargs,
    )
