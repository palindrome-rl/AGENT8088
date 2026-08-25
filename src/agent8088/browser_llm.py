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
from typing import Any, Optional

from browser_use.llm.litellm import ChatLiteLLM


@dataclass
class Agent8088ChatModel(ChatLiteLLM):
    budget: Optional[Any] = None  # duck-typed engine._TurnBudget: .exceeded() / .add_tokens()

    async def ainvoke(self, messages, output_format=None, **kwargs):
        if self.budget is not None:
            over = self.budget.exceeded()
            if over:
                raise RuntimeError(over)
        result = await super().ainvoke(messages, output_format, **kwargs)
        if self.budget is not None and result.usage is not None:
            self.budget.add_tokens(result.usage.prompt_tokens, result.usage.completion_tokens)
        return result


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
