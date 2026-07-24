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
