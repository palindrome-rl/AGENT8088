import json

from tests.conftest import ScriptedModel


def test_find_tool_calls_respects_allowed_set(engine):
    text = '✿FUNCTION✿: execute_shell ✿ARGS✿: {"command": "ls"}'
    # Allowed everywhere by default:
    assert engine.find_tool_calls(text)[0]["name"] == "execute_shell"
    # Disallowed when not in the allowed set:
    assert engine.find_tool_calls(text, allowed={"read_text"}) == []


def test_find_tool_calls_alias_then_restrict(engine):
    text = '✿FUNCTION✿: bash ✿ARGS✿: {"command": "ls"}'
    # 'bash' resolves to execute_shell, which is allowed here:
    assert engine.find_tool_calls(text, allowed={"execute_shell"})[0]["name"] == "execute_shell"
    # ...but rejected when execute_shell is not allowed:
    assert engine.find_tool_calls(text, allowed={"read_text"}) == []


def test_run_agent_uses_custom_system_prompt_and_depth(engine):
    fake = ScriptedModel(["Hello from the sub-agent."])
    engine.create_completion = fake  # monkeypatch module global

    msgs = [{"role": "user", "content": "say hi"}]
    answer = engine.run_agent(
        msgs, max_turns=3, system_prompt="CUSTOM-PROMPT", depth=1,
    )
    assert answer == "Hello from the sub-agent."
    # The custom system prompt was forwarded to create_completion:
    assert fake.calls[0]["kwargs"].get("system_prompt") == "CUSTOM-PROMPT"


def test_load_subagent_specs_parses_frontmatter(engine, tmp_path):
    d = tmp_path / "agents"
    d.mkdir()
    (d / "explore.md").write_text(
        "---\n"
        "name: explore\n"
        "description: Read-only searcher\n"
        "tools: read_text, execute_shell\n"
        "max_turns: 6\n"
        "---\n"
        "You are a read-only exploration sub-agent.\n"
    )
    specs = engine.load_subagent_specs(d)
    assert "explore" in specs
    p = specs["explore"]
    assert p["description"] == "Read-only searcher"
    assert p["tools"] == ["read_text", "execute_shell"]
    assert p["max_turns"] == 6
    assert p["system_prompt"].startswith("You are a read-only")


def test_load_subagent_specs_missing_dir_has_default(engine, tmp_path):
    specs = engine.load_subagent_specs(tmp_path / "nope")
    assert "general-purpose" in specs  # built-in fallback


def test_exec_subagent_depth_guard(engine):
    # At/above max depth, refuse immediately (SUBAGENT_MAX_DEPTH default 1).
    out = engine._exec_subagent({"task": "do a thing"}, depth=engine.SUBAGENT_MAX_DEPTH)
    assert "depth" in out.lower()


def test_exec_subagent_happy_path_and_isolation(engine, monkeypatch):
    # Sub-agent immediately produces a final answer (no tool calls).
    monkeypatch.setattr(engine, "create_completion",
                        ScriptedModel(["Found 3 TODOs in the repo."]))
    engine._last_tool_output = "PARENT-OUTPUT"  # parent state must survive
    engine._last_tool_name = "execute_shell"

    out = engine._exec_subagent(
        {"agent_type": "general-purpose", "task": "count the TODOs"}, depth=0,
    )
    assert "Found 3 TODOs" in out
    assert "general-purpose" in out  # result is labeled with the agent type
    # Parent's last-output store was restored after the sub-run:
    assert engine._last_tool_output == "PARENT-OUTPUT"
    assert engine._last_tool_name == "execute_shell"


def test_exec_subagent_unknown_type_falls_back(engine, monkeypatch):
    monkeypatch.setattr(engine, "create_completion", ScriptedModel(["ok"]))
    out = engine._exec_subagent({"agent_type": "does-not-exist", "task": "x"}, depth=0)
    assert "unknown agent_type" in out.lower()


def test_exec_subagent_ui_hooks_fire(engine, monkeypatch):
    monkeypatch.setattr(engine, "create_completion", ScriptedModel(["all done"]))
    events = {"factory": [], "done": []}

    def factory(agent_type, task, depth):
        events["factory"].append((agent_type, task, depth))
        return {"done": lambda answer: events["done"].append(answer)}

    monkeypatch.setattr(engine, "subagent_ui", factory)
    out = engine._exec_subagent({"agent_type": "explore", "task": "look around"}, depth=0)

    assert events["factory"] == [("explore", "look around", 0)]
    assert events["done"] == ["all done"]
    assert "all done" in out
