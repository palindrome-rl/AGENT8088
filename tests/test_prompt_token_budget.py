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
    """Worst case: tool_selection=full, so every schema ships.

    Phase 1 (drop the duplicate catalogue) measured 14,403, down from 17,958.
    The saving is 3,555 rather than the full 3,994 of render_tool_docs,
    because the calling convention and the mandatory-routing block are
    deliberately retained -- the schema does not carry them.

    The ceiling then moved 14,600 -> 14,750 to buy the tool name index (~250
    tokens, all 48 names). That is a deliberate purchase, not drift: without it
    a tool dropped by hybrid selection is invisible rather than merely
    unexpanded, and the model reaches for it blind. A live A/B measured that
    failure at 8 turns / 994 tokens against 4 turns / 513 tokens -- i.e. the
    blind path costs far more than the index it replaces. See
    test_hybrid_request_is_the_path_that_actually_ships for the default mode,
    which is what the 11,000 target was really about.
    """
    prompt = E.current_system_prompt(native_tools=True)
    schema = json.dumps(E.build_tools_def(E.TOOL_SPECS))
    total = approx_tokens(prompt) + approx_tokens(schema)
    assert total < 14_750, f"fixed overhead regressed to {total} tokens"


def test_hybrid_request_is_the_path_that_actually_ships():
    """The default is tool_selection=hybrid, so a real request carries the
    prompt plus the SELECTED schemas, not all 48.

    Measured 10,450 with the pinned+always set, against the 11,000 target the
    budget work set for this phase. Guarding the realistic path matters more
    than guarding the worst case, because this is the number every default
    request actually pays.
    """
    prompt = E.current_system_prompt(native_tools=True)
    selected = {name: E.TOOL_SPECS[name]
                for name in (E._TOOL_SELECTION_PINNED | E._TOOL_SELECTION_ALWAYS)
                if name in E.TOOL_SPECS}
    schema = json.dumps(E.build_tools_def(selected))
    total = approx_tokens(prompt) + approx_tokens(schema)
    assert total < 11_000, f"hybrid request overhead regressed to {total} tokens"


def test_every_tool_name_is_visible_to_native_providers():
    """Selection narrows schemas; it must never narrow awareness. A model that
    does not know a tool exists cannot ask for its schema -- it guesses."""
    prompt = E.current_system_prompt(native_tools=True)
    for name in E.TOOL_SPECS:
        assert name in prompt, f"{name} is invisible to native-tool providers"


def test_cli_session_prompt_follows_the_provider(monkeypatch):
    """The CLI builds its own prompt and passes it as system_prompt=, which
    outranks engine.current_system_prompt() at engine.py:2116.

    Patching only the engine therefore saved nothing on the CLI path -- caught
    by live testing, not by the unit tests above. Drive the provider decision
    explicitly rather than depending on whatever provider the test environment
    happens to have configured.
    """
    import agent8088.cli as C

    monkeypatch.setattr(E, "_native_tools_enabled", lambda *a, **k: True)
    native = C._session_system_prompt()
    assert "- execute_shell(" not in native
    assert "Mandatory routing" in native

    monkeypatch.setattr(E, "_native_tools_enabled", lambda *a, **k: False)
    prose = C._session_system_prompt()
    assert "- execute_shell(" in prose

    assert len(native) < len(prose), "native-tool prompt must be the smaller one"
