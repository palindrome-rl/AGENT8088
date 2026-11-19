"""Qwen3 XML tool calls written into the message text are still executed if
the server's tool parser did not lift them into tool_calls."""


XML_CALL = (
    "I'll list the directory.\n<tool_call>\n<function=execute_shell>\n"
    "<parameter=command>\nls -la /app\n</parameter>\n</function>\n</tool_call>"
)


def test_single_xml_call_is_parsed(engine):
    calls = engine.find_tool_calls(XML_CALL, {"execute_shell"})
    assert calls == [{"name": "execute_shell", "arguments": {"command": "ls -la /app"}}]


def test_multiple_parameters_and_calls(engine):
    text = (
        "<tool_call>\n<function=write_file>\n<parameter=filename>\n/app/a.py\n</parameter>\n"
        "<parameter=content>\nprint('x')\nprint('y')\n</parameter>\n</function>\n</tool_call>\n"
        "<tool_call>\n<function=execute_shell>\n<parameter=command>\npython3 /app/a.py\n"
        "</parameter>\n<parameter=timeout>\n60\n</parameter>\n</function>\n</tool_call>"
    )
    calls = engine.find_tool_calls(text, {"write_file", "execute_shell"})
    assert calls == [
        {"name": "write_file", "arguments": {"filename": "/app/a.py", "content": "print('x')\nprint('y')"}},
        {"name": "execute_shell", "arguments": {"command": "python3 /app/a.py", "timeout": "60"}},
    ]


def test_unknown_function_is_ignored(engine):
    text = "<function=rm_everything>\n<parameter=x>\n1\n</parameter>\n</function>"
    assert engine.find_tool_calls(text, {"execute_shell"}) == []
