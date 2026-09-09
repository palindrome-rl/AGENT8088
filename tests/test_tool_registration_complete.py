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
