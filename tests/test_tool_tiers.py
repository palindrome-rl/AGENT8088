"""Two-tier tool advertising.

All 48 tool schemas went out on every request (~5,646 tokens). The Claude
Agent SDK's tool-search design notes two problems with that: tool definitions
eat the context window, and "tool selection accuracy degrades with more than
30-50 tools loaded at once". agent8088 sat at 48.

Core-tier tools keep their full schema upfront. The rest are advertised as one
index line each and their schemas load on demand through tool_search.

Deferral is an advertising change, not a permission change: run_tool gates on
TOOL_NAMES, which still holds all 48.
"""
import json

import agent8088.engine as E


# --- tier parsing ----------------------------------------------------------

def test_spec_defaults_to_deferred():
    """A newly added tool must not silently re-inflate every request."""
    spec = E._build_spec("some_tool", {}, {}, "A tool")
    assert spec["tier"] == "deferred"


def test_spec_reads_core_tier_from_tools_txt():
    spec = E._build_spec("some_tool", {"tier": "core"}, {}, "A tool")
    assert spec["tier"] == "core"


def test_config_can_promote_a_tool_to_core():
    """A user who leans on one tool constantly should be able to pin it."""
    spec = E._build_spec("some_tool", {}, {"tool_tier.some_tool": "core"}, "A tool")
    assert spec["tier"] == "core"


# --- the shipped tiering ---------------------------------------------------

EXPECTED_CORE = {
    "execute_shell", "read_text", "write_file", "web_search", "last_output",
    "document_read", "calculate", "describe_capabilities", "spawn_subagent",
    "browse_page", "tool_search",
}


def test_shipped_core_tier_is_what_was_agreed():
    core = {n for n, s in E.TOOL_SPECS.items() if s.get("tier") == "core"}
    assert core == EXPECTED_CORE, f"core tier drifted: {core ^ EXPECTED_CORE}"


def test_most_tools_are_deferred():
    deferred = [n for n, s in E.TOOL_SPECS.items() if s.get("tier") != "core"]
    assert len(deferred) >= 30, f"only {len(deferred)} deferred -- tiering not applied?"


# --- the deferred index ----------------------------------------------------

def test_deferred_index_lists_deferred_tools_only():
    index = E.render_deferred_tool_index(E.TOOL_SPECS)
    assert "git_push" in index
    assert "- execute_shell:" not in index   # core tools are not re-listed
    assert "tool_search" in index            # the model is told how to load one


def test_deferred_index_is_cheap():
    index = E.render_deferred_tool_index(E.TOOL_SPECS)
    assert len(index) // 4 < 900, f"index cost {len(index)//4} tokens"


def test_deferred_index_is_empty_when_nothing_is_deferred():
    """No index section at all rather than an empty heading."""
    specs = {n: s for n, s in E.TOOL_SPECS.items() if s.get("tier") == "core"}
    assert E.render_deferred_tool_index(specs) == ""


# --- tool_search -----------------------------------------------------------

def test_tool_search_finds_a_deferred_tool_by_capability():
    out = E.run_tool("tool_search", {"query": "push commits to a git remote"})
    assert "git_push" in out


def test_tool_search_returns_at_most_five():
    """The SDK loads up to 5 per search; more gives back what deferral saved."""
    out = E.run_tool("tool_search", {"query": "git branch remote push commit checkout"})
    assert out.count("Arguments") <= 5 or out.count("git_") <= 5


def test_tool_search_needs_a_query():
    out = E.run_tool("tool_search", {"query": ""})
    assert "quer" in out.lower()


def test_tool_search_reports_a_miss_without_inventing_a_tool():
    out = E.run_tool("tool_search", {"query": "zzzz qqqq wwww vvvv"})
    assert "No tool" in out or "no tool" in out


def test_tool_search_does_not_reload_an_already_loaded_core_tool():
    """Returning a core tool's schema spends exactly what deferral saved."""
    out = E.run_tool("tool_search", {"query": "run a shell command"})
    assert "already-loaded" in out or "execute_shell" not in out.split("name")[0]


def test_a_deferred_tool_stays_callable_without_searching_first():
    """Deferral changes what is advertised, never what is executable.

    run_tool gates on TOOL_NAMES, which still holds all 48.
    """
    assert "git_push" in E.TOOL_NAMES
    assert "add_mcp_server" in E.TOOL_NAMES


# --- request assembly ------------------------------------------------------

def test_deferral_is_off_by_default():
    """Opt-in, on live evidence.

    ornith-1.0-35b would not reach for tool_search: it hand-rolled shell
    probing instead, searching in 1 of 6 runs even with the instruction in the
    mandatory-routing block. Baseline called the right tool first, 3 of 3. A
    model that will not search turns the token saving into a capability
    regression, so the default must not change behaviour for existing users.
    """
    assert E.TOOL_SEARCH_ENABLED is False
    names = {t["function"]["name"] for t in E.build_tools_def(E.TOOL_SPECS)}
    assert "git_push" in names, "default must advertise every tool"
    assert len(names) >= 40


def test_deferral_works_when_switched_on():
    names = {t["function"]["name"]
             for t in E.build_tools_def(E.TOOL_SPECS, defer=True)}
    assert "execute_shell" in names
    assert "git_push" not in names


def test_schema_payload_shrinks_when_deferral_is_on():
    before = len(json.dumps(E.build_tools_def(E.TOOL_SPECS, defer=False))) // 4
    after = len(json.dumps(E.build_tools_def(E.TOOL_SPECS, defer=True))) // 4
    assert after < before / 2, f"schema only went {before} -> {after}"


def test_tool_search_tells_the_model_the_tools_are_now_callable():
    """Live failure this pins: the model called tool_search, got
    check_local_hardware's schema, then ran `execute_shell check_local_hardware`
    -- treating the tool name as a shell binary (sh: not found, exit 127).

    describe_tool's own framing says "Description only; the tool was not
    executed", which reads as "you may not call this". tool_search must
    override that: the point of searching is that the tool is now callable.
    """
    out = E.run_tool("tool_search", {"query": "check local hardware RAM GPU"})
    assert "check_local_hardware" in out
    lowered = out.lower()
    assert "now loaded" in lowered or "call it directly" in lowered, (
        "tool_search result must say the tools are callable"
    )
    assert "not executed" not in lowered, (
        "describe_tool's 'was not executed' framing must not leak into a "
        "tool_search result -- it made the model shell out instead of calling"
    )
    assert "execute_shell" in lowered or "shell" in lowered, (
        "must explicitly warn against running the name as a shell command"
    )
