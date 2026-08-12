"""Sub-agent answers are bounded and honest, and the system prompt has no
in-session viewer.

Both behaviours come from the same review: a sub-agent reported "no SSRF
protection exists" about a tree that has an SSRF guard, a dedicated test module
and a documented section on it. Every one of its searches had failed, and it
presented that absence as a finding — in a full report dumped verbatim into the
parent's context.
"""
import pytest

from agent8088 import engine as A


# --- Answer cap -------------------------------------------------------------

def test_short_answer_is_returned_unchanged():
    assert A._cap_subagent_answer("found it in engine.py:41") == "found it in engine.py:41"


def test_long_answer_is_truncated_and_says_so():
    answer = "x" * (A.MAX_SUBAGENT_ANSWER_CHARS + 500)
    capped = A._cap_subagent_answer(answer)

    assert len(capped) < len(answer)
    # A silent truncation reads to the parent model as a complete answer, which
    # is the failure this guards against — the marker is the point, not the cap.
    assert "truncated" in capped
    assert "500 more characters" in capped


def test_cap_keeps_the_head_not_the_tail():
    """A sub-agent puts its finding first, so the tail is what may be dropped."""
    answer = "THE FINDING IS HERE" + ("filler " * A.MAX_SUBAGENT_ANSWER_CHARS)
    assert A._cap_subagent_answer(answer).startswith("THE FINDING IS HERE")


def test_cap_can_be_disabled(monkeypatch):
    monkeypatch.setattr(A, "MAX_SUBAGENT_ANSWER_CHARS", 0)
    answer = "y" * 50_000
    assert A._cap_subagent_answer(answer) == answer


def test_boundary_length_is_not_truncated():
    exact = "z" * A.MAX_SUBAGENT_ANSWER_CHARS
    assert A._cap_subagent_answer(exact) == exact


def test_exec_subagent_actually_applies_the_cap(monkeypatch):
    """The cap must be wired into the return path, not merely available.

    Testing _cap_subagent_answer alone passes even when nothing calls it, which
    is exactly the hole a unit test of a helper leaves open.
    """
    monkeypatch.setattr(A, "run_agent", lambda messages, **kw: "q" * 50_000)
    result = A._exec_subagent({"agent_type": "explore", "task": "anything"})

    assert len(result) < 50_000
    assert "truncated" in result


# --- Reporting contract -----------------------------------------------------

def test_every_profile_gets_the_reporting_contract(monkeypatch):
    """The contract is appended in the engine, not copied into each profile, so
    a newly added profile cannot ship without it."""
    seen = {}

    def fake_run_agent(messages, **kw):
        seen["system"] = kw.get("system_prompt", "")
        return "done"

    monkeypatch.setattr(A, "run_agent", fake_run_agent)
    A._exec_subagent({"agent_type": "explore", "task": "look at something"})

    contract = seen["system"]
    assert A._SUBAGENT_REPORTING_CONTRACT in contract
    assert "VERIFIED" in contract and "INFERRED" in contract
    # The profile's own instructions survive alongside it.
    assert "read-only exploration sub-agent" in contract


def test_contract_names_the_failure_it_prevents():
    """Wording the model has to act on, not a vague instruction to be careful."""
    contract = A._SUBAGENT_REPORTING_CONTRACT
    assert "failed" in contract.lower()
    assert "NOT evidence of" in contract
    assert "directory you actually inspected" in contract


# --- No in-session system-prompt viewer -------------------------------------

def test_no_slash_command_prints_the_system_prompt():
    """`/system` printed A.SYSTEM_PROMPT verbatim, so the model-side refusal was
    six keystrokes away from being pointless."""
    from agent8088 import cli

    assert "system" not in cli.COMMANDS
    assert not hasattr(cli, "cmd_system")


def test_no_registered_command_is_a_prefix_route_to_the_prompt():
    """Commands are prefix-matched, so a leftover alias would re-open the route."""
    from agent8088 import cli

    assert not [name for name in cli.COMMANDS if "system" in name]


@pytest.mark.parametrize("surface", ["cli", "gateway"])
def test_system_prompt_is_not_exposed_on_any_surface(surface):
    if surface == "cli":
        from agent8088 import cli
        assert "system" not in cli.COMMANDS
    else:
        from agent8088.gateway import runner
        assert not [c for c in runner.SLASH_COMMANDS if "system" in c]
