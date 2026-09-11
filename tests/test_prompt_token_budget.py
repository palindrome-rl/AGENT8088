"""Regression guards on fixed per-request token overhead.

Measured on origin/development @ f604c3e2, custom OpenAI-compatible endpoint:
    system prompt   12,312 tok   (of which text tool catalogue 3,994)
    tools schema     5,646 tok   (48 tools)
    TOTAL           17,958 tok   = 55% of a 32,768 context window

The 48 tools were described twice on every request to every provider except
ollama: once as prose inside the system prompt, once as the JSON schema array.
"""
import json

import agent8088.engine as E


def approx_tokens(text: str) -> int:
    """chars/4 -- good enough for budgeting, not an exact API token count."""
    return len(text) // 4


def test_native_tool_providers_do_not_get_the_text_tool_catalogue():
    """With native tool calling the JSON schema is the tool contract.

    Repeating all 48 tools as prose costs ~4,000 tokens per request and gives
    the model two descriptions of one tool that can drift apart.
    """
    prompt = E.current_system_prompt(native_tools=True)
    assert "- execute_shell(" not in prompt
    assert "- web_search(" not in prompt


def test_the_calling_convention_survives_for_native_tool_providers():
    """Only the per-tool catalogue goes. The routing rules and the calling
    convention are behavioural instructions the schema does not carry."""
    prompt = E.current_system_prompt(native_tools=True)
    assert "Mandatory routing" in prompt
    assert "## Tools" in prompt


def test_ollama_still_gets_the_text_tool_catalogue():
    """Ollama rejects the OpenAI tools param -- the prompt is its ONLY source
    of tool knowledge. Removing the catalogue there breaks tool calling."""
    prompt = E.current_system_prompt(native_tools=False)
    assert "- execute_shell(" in prompt
    assert "- web_search(" in prompt


def test_fixed_overhead_within_budget():
    prompt = E.current_system_prompt(native_tools=True)
    schema = json.dumps(E.build_tools_def(E.TOOL_SPECS))
    total = approx_tokens(prompt) + approx_tokens(schema)
    assert total < 14_000, f"fixed overhead regressed to {total} tokens"
