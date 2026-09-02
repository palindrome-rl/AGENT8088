import json

from agent8088.engine import render_tool_docs


def test_browser_workflow_uses_one_session():
    prompt = render_tool_docs({
        "browse_page": {"args": ["url", "task"], "description": "Browse a page."},
    })

    assert "entire end-to-end workflow" in prompt
    assert "each call starts a fresh browser session" in prompt


def test_browser_receives_the_complete_user_goal(engine, monkeypatch):
    responses = iter((
        '✿FUNCTION✿: browse_page ✿ARGS✿: {"url":"https://example.com",'
        '"task":"only log in"}',
        "done",
    ))
    executed = []

    def completion(*_args, **_kwargs):
        message = type("Message", (), {"content": next(responses)})()
        choice = type("Choice", (), {"message": message, "finish_reason": "stop"})()
        return type("Response", (), {"choices": [choice]})()

    monkeypatch.setattr(engine, "_create_completion_with_fallback", completion)
    monkeypatch.setattr(
        engine, "exec_tool",
        lambda name, raw_args, **_kwargs: executed.append((name, raw_args)) or "complete",
    )
    goal = "Log in, add the cheapest item, and complete checkout."

    assert engine.run_agent(
        [{"role": "user", "content": goal}], max_turns=2,
        system_prompt="", tools_def=[], allowed_tools={"browse_page"},
    ) == "done"
    assert executed
    assert goal in executed[0][1]
    assert "only log in" not in executed[0][1]
    assert "do not navigate directly" in executed[0][1]
    assert "retry typing only if the site shows a validation error" in executed[0][1]


def test_browser_can_fill_details_missing_from_search_results(engine, monkeypatch):
    responses = iter((
        '✿FUNCTION✿: web_search ✿ARGS✿: '
        '{"query":"central London barbers prices contact details"}',
        '✿FUNCTION✿: browse_page ✿ARGS✿: '
        '{"url":"https://example.com/prices","task":"Extract haircut prices and phone number."}',
        "done",
    ))
    executed = []

    def completion(*_args, **_kwargs):
        message = type("Message", (), {"content": next(responses)})()
        choice = type("Choice", (), {"message": message, "finish_reason": "stop"})()
        return type("Response", (), {"choices": [choice]})()

    def exec_tool(name, raw_args, **_kwargs):
        executed.append((name, json.loads(raw_args)))
        return "Search results" if name == "web_search" else "Haircut: £30; phone: 020 0000 0000"

    monkeypatch.setattr(engine, "_create_completion_with_fallback", completion)
    monkeypatch.setattr(engine, "exec_tool", exec_tool)

    assert engine.run_agent(
        [{"role": "user", "content": (
            "List central London barbershops with haircut prices and contact info."
        )}],
        max_turns=3, system_prompt="", tools_def=[],
        allowed_tools={"web_search", "browse_page"},
    ) == "done"
    assert [name for name, _args in executed] == ["web_search", "browse_page"]


def test_resumed_browser_task_keeps_its_original_goal(engine, monkeypatch):
    response = '✿FUNCTION✿: browse_page ✿ARGS✿: {"url":"https://example.com/later"}'
    message = type("Message", (), {"content": response})()
    choice = type("Choice", (), {"message": message, "finish_reason": "stop"})()
    monkeypatch.setattr(
        engine, "_create_completion_with_fallback",
        lambda *_args, **_kwargs: type("Response", (), {"choices": [choice]})(),
    )
    executed = []
    monkeypatch.setattr(
        engine, "exec_tool",
        lambda _name, raw_args, **_kwargs: executed.append(raw_args) or "complete",
    )

    engine.run_agent([
        {"role": "user", "content": (
            "This is a durable task. End with TASK_COMPLETE.\n\n"
            "Start at https://example.com/ and complete checkout."
        )},
        {"role": "user", "content": "Continue this durable task from the checkpoint."},
    ], max_turns=1, system_prompt="", tools_def=[], allowed_tools={"browse_page"})

    arguments = json.loads(executed[0])
    assert "complete checkout" in arguments["task"]
    assert "This is a durable task" not in arguments["task"]
    assert "TASK_COMPLETE" not in arguments["task"]
    assert arguments["url"] == "https://example.com/"
