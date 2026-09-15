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

    14,750 -> 16,000 -> 11,100. The middle number was measured while merging
    this branch, before the layered prompt landed; it is recorded here only
    because the reasoning still holds and the arithmetic does not.

    Three things had pushed the worst case up: drift predating all of it
    (development @ e416abcc measured 14,887, mostly the archify catalogue
    entry, ~193); repository_read, ~375; and the three tools added here.
    Then the layered system prompt cut the shared half by roughly half again,
    and the merged tree measures 10,818 at 52 tools.

    So the ceiling comes DOWN rather than up. Leaving it at 16,000 would have
    left 48% slack -- the prompt could nearly double and this would still
    pass, which is not a budget. 11,100 keeps ~2.6% headroom: enough that a
    one-line description edit does not fail it, little enough that a real
    regression does.

    The repository_read purchase still carries its evidence: a live A/B on
    glm-5.3, orientation question against docs/, cost 51 tool calls over
    872.9s and a wrong answer without it, against 4 calls over 154.7s and the
    correct one with it.

    11,100 -> 12,000 for review_code, and part of that is not this tool: the
    repository-map work landed alongside it, so the 10,818 measured when this
    ceiling was last set is now 11,656 at 55 tools. review_code's own schema
    is 233 of that.

    What it buys, same repository and same model: a review through
    OpenCodeReview's own engine took 4 turns and 614 prompt tokens, against 12
    turns and 1,031 for the delegated path that reviews the diff in this
    agent's own context, and 19 calls / 4,098 tokens before the diff was
    attached at all.
    """
    prompt = E.current_system_prompt(native_tools=True)
    schema = json.dumps(E.build_tools_def(E.TOOL_SPECS))
    total = approx_tokens(prompt) + approx_tokens(schema)
    assert total < 12_000, f"fixed overhead regressed to {total} tokens"


def test_hybrid_request_is_the_path_that_actually_ships():
    """The default is tool_selection=hybrid, so a real request carries the
    prompt plus the SELECTED schemas, not all 48.

    Measured 10,450 with the pinned+always set, against the 11,000 target the
    budget work set for this phase. Guarding the realistic path matters more
    than guarding the worst case, because this is the number every default
    request actually pays.

    Ceiling then moved 11,000 -> 11,100 to buy three pinned tools: edit_file,
    run_tests and generate_tests. Measured 11,027 (from 10,663 on the branch
    point). That is a deliberate purchase of 364 tokens, ~0.25% of the request:
    an unpinned edit_file can be ranked away by a prompt like "fix the bug in
    parser.py", after which the model falls back to write_file and overwrites
    the whole file -- losing every line it did not retype. The schema costs a
    rounding error; the miss costs the file. generate_tests is named in the
    turn-end nudge, so it must be exposed for the same reason the
    mandatory-routing tools are (see the _TOOL_SELECTION_PINNED comment).

    The three descriptions were trimmed hard before this ceiling moved, which
    bought back ~220 tokens. Trimming further starts removing guidance the
    model needs to use edit_file correctly at all.

    11,100 -> 6,600 on merging into development. The realistic path was
    always the one worth guarding, and the layered prompt is felt here most:
    the same pinned+always set now measures 6,351, against 11,027 on this
    branch's original base. repository_read and archify are both in that
    figure; they cost what they cost, and the shared prompt around them got
    much smaller.

    6,600 rather than 11,500 for the same reason as above: a ceiling with 81%
    slack is decoration.

    6,600 -> 7,000 for review_code, with the same caveat that the repo-map
    tools are part of the move: 6,351 -> 6,655 at 55 tools. review_code is not
    pinned, so this is the tool index entry and the shared prompt growing, not
    its schema shipping on every request.

    """
    prompt = E.current_system_prompt(native_tools=True)
    selected = {name: E.TOOL_SPECS[name]
                for name in (E._TOOL_SELECTION_PINNED | E._TOOL_SELECTION_ALWAYS)
                if name in E.TOOL_SPECS}
    schema = json.dumps(E.build_tools_def(selected))
    total = approx_tokens(prompt) + approx_tokens(schema)
    assert total < 7_000, f"hybrid request overhead regressed to {total} tokens"


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
