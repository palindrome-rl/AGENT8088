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

    14,750 -> 16,000. Three things moved it, and they are independent:

    1. Drift that predates all of it. development @ e416abcc measures 14,887,
       so this assertion was already red on a clean checkout and guarding
       nothing. Most of that is the archify skill: one catalogue entry, ~193,
       the body staying out because the skill is progressive.
    2. repository_read, ~375. 7th largest of 49 schemas at the time, in line
       with document_read which it parallels, and its nine arguments are the
       API its design note specified.
    3. The diff-edit and autotest tools here, three more (52 total), with
       their schemas in this worst case and their names in the tool index.

    Measured on the merged tree: 15,713. The ceiling sits at 16,000 rather
    than snug against that, because a ceiling with no headroom fails on the
    next one-line description edit and gets raised reflexively, which is how
    a budget stops being one.

    2 and 3 both carry the evidence the tool index purchase did. For
    repository_read, a live A/B on glm-5.3 -- an orientation question against
    docs/ cost 51 tool calls over 872.9s and returned a wrong answer without
    it, against 4 calls over 154.7s and the correct one with it. Paying fixed
    tokens to avoid 40 read_text round trips is that same trade one layer up.
    """
    prompt = E.current_system_prompt(native_tools=True)
    schema = json.dumps(E.build_tools_def(E.TOOL_SPECS))
    total = approx_tokens(prompt) + approx_tokens(schema)
    assert total < 16_000, f"fixed overhead regressed to {total} tokens"


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

    11,100 -> 11,500 on merging into development. The 11,027 above was measured
    on a branch point that did not yet carry repository_read or the archify
    skill; with both, the same pinned+always set measures 11,246. No tool was
    added by the merge itself -- this is the two prior purchases showing up in
    the realistic path as well as the worst case, which is what they were
    always going to do.

    """
    prompt = E.current_system_prompt(native_tools=True)
    selected = {name: E.TOOL_SPECS[name]
                for name in (E._TOOL_SELECTION_PINNED | E._TOOL_SELECTION_ALWAYS)
                if name in E.TOOL_SPECS}
    schema = json.dumps(E.build_tools_def(selected))
    total = approx_tokens(prompt) + approx_tokens(schema)
    assert total < 11_500, f"hybrid request overhead regressed to {total} tokens"


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
