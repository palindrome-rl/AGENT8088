"""Every tool the engine can dispatch must be reachable by the model.

The engine dispatches on a tool name; `tools.txt` is what tells the model the
tool exists. Nothing tied the two together, so a stale `tools.txt` could drop a
registration while leaving the implementation in place. The tool then works
when called directly and is invisible to the model -- passing unit tests, dead
feature.

That happened twice from one commit: `git_remote` (#155) and `describe_tool`
(#153) were both dropped by a branch that predated them and overwrote the file.
Seven suite failures, one cause, and the git fix the QA report asked for was
inert on development while looking merged.
"""
import re

# Names the engine special-cases in run_tool by string comparison. Each one is
# a feature the model can only use if tools.txt also declares it.
_DISPATCHED = re.compile(r'name == "([a-z_]+)"')


def _registered(engine):
    return set(engine.TOOL_SPECS)


def test_every_dispatched_tool_is_registered(engine):
    source = (engine.APP_DIR / "engine.py").read_text(encoding="utf-8")
    dispatched = set(_DISPATCHED.findall(source))
    registered = _registered(engine)
    # Only names that look like tools: a dispatched name that is registered
    # somewhere in history, or that run_tool returns a result for.
    candidates = {n for n in dispatched if n.startswith(("git_", "describe_", "cli_anything_"))}
    missing = sorted(candidates - registered)
    assert not missing, (
        f"engine dispatches these but tools.txt does not declare them, so the "
        f"model cannot call them: {missing}")


def test_the_two_that_were_lost_are_present(engine):
    """Named explicitly, so a future revert of tools.txt fails loudly here."""
    for name in ("git_remote", "describe_tool"):
        assert name in engine.TOOL_SPECS, f"{name} lost from tools.txt again"


def test_git_push_still_offers_a_remote_and_branch(engine):
    """The argv builder accepting them is useless if the schema hides them."""
    args = engine.TOOL_SPECS["git_push"].get("args") or []
    for expected in ("remote", "branch", "set_upstream"):
        assert expected in args, (
            f"git_push no longer offers '{expected}' to the model, so the "
            f"argv builder's support for it is unreachable")


def test_every_registered_tool_reaches_the_model(engine):
    """TOOLS_DEF is what is actually sent; TOOL_SPECS alone is not enough."""
    offered = {t["function"]["name"] for t in engine.build_tools_def(engine.TOOL_SPECS)}
    assert "git_remote" in offered
    assert "describe_tool" in offered


# --- arguments count too, not just tool names ------------------------------

# A tool the engine reads an argument from, and the argument it must therefore
# declare. Restoring a tools.txt line from the wrong revision silently drops
# these: the engine still honours the argument, the schema stops offering it,
# and the tests for the implementation keep passing. That is how write_file
# lost source_url in the very commit that fixed the same bug for git_remote.
_REQUIRED_ARGS = {
    "write_file": ["filename", "content", "source_url"],
    "git_push": ["remote", "branch", "set_upstream"],
    "git_remote": ["action", "name", "url"],
}


def test_tools_declare_the_arguments_their_implementation_reads(engine):
    missing = {}
    for tool, expected in _REQUIRED_ARGS.items():
        declared = set(engine.TOOL_SPECS.get(tool, {}).get("args") or [])
        gap = [a for a in expected if a not in declared]
        if gap:
            missing[tool] = gap
    assert not missing, (
        f"the engine reads these arguments but the schema does not offer them, "
        f"so the model cannot pass them: {missing}")


def test_write_file_can_still_be_told_a_source_url(engine):
    """Named on its own: this one was lost and restored twice in two days."""
    args = engine.TOOL_SPECS["write_file"].get("args") or []
    assert "source_url" in args, (
        "write_file no longer offers source_url, so fetching remote/binary "
        "content is unreachable from the model")


def test_git_diff_sends_branch_questions_to_the_reviewer(engine):
    """Reachable is not the same as preferred, and the wrong pick answered wrongly.

    Asked whether a branch was safe to ship, a live run reached for `git_diff`,
    read the working tree -- which is not the branch -- and reported "safe to
    ship" while the branch carried four planted defects. `review_code` was in
    the selected tool set the whole time; the model simply did not prefer it.

    The pointer belongs on `git_diff` because that is where the hand lands, the
    same way `git_checkout` already redirects away from `execute_shell`.
    """
    git_diff = engine.TOOL_SPECS["git_diff"]["description"]

    assert "review_code" in git_diff, "the redirect is the whole point"
    assert "working-tree" in git_diff, "the wrong answer came from diffing the wrong thing"


def test_system_prompt_makes_review_routing_and_recovery_unambiguous(engine):
    prompt = engine.current_system_prompt(native_tools=True)

    assert "MUST call review_code" in prompt
    assert "suggested_action" in prompt
    assert "never substitute an ad-hoc review" in prompt
    assert "never invent admin rights" in prompt
    assert "For zero findings" in prompt
    assert "never invent changes" in prompt
    assert "Cross-check findings against diff_evidence" in prompt
