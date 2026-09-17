import json

from agent8088.engine import render_tool_docs


def test_browser_workflow_uses_one_session():
    prompt = render_tool_docs({
        "browse_page": {"args": ["url", "task"], "description": "Browse a page."},
    })

    assert "entire end-to-end workflow" in prompt
    assert "each call starts a fresh browser session" in prompt


def test_download_requests_route_to_write_file_source_url():
    prompt = render_tool_docs({
        "browse_page": {"args": ["url", "task"], "description": "Browse a page."},
        "write_file": {"args": ["filename", "content", "source_url"], "description": "Write a file."},
    })

    assert "MUST call write_file with source_url" in prompt


def test_browser_system_message_treats_dead_host_as_terminal():
    """The browser sub-agent must stop after repeated navigation failures to
    the same host instead of wandering to other sites -- live regression:
    a browse_page on a connection-closed host ended up 'visiting
    mtalk.google.com' for the remaining steps."""
    import inspect
    from agent8088 import engine

    src = inspect.getsource(engine._run_browser_agent)
    assert "dead or unreachable" in src
    assert "stop the task" in src


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


def test_browser_can_fill_details_after_an_explicit_browser_request(engine, monkeypatch):
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
            "List central London barbershops with haircut prices and contact info. "
            "Use a browser to inspect their pages if search results omit a price."
        )}],
        max_turns=3, system_prompt="", tools_def=[],
        allowed_tools={"web_search", "browse_page"},
    ) == "done"
    assert [name for name, _args in executed] == ["web_search", "browse_page"]
    assert executed[1][1]["task"] == "Extract haircut prices and phone number."


def test_visit_attractions_does_not_authorize_browser(engine, monkeypatch):
    responses = iter((
        '✿FUNCTION✿: web_search ✿ARGS✿: {"query":"Abu Dhabi attractions hours"}',
        '✿FUNCTION✿: browse_page ✿ARGS✿: '
        '{"url":"https://example.com/hotel","task":"Check room prices."}',
        "I could not verify live room availability from the search results.",
    ))
    exposed = []
    executed = []

    def completion(*_args, **kwargs):
        exposed.append(kwargs.get("tools"))
        message = type("Message", (), {"content": next(responses)})()
        choice = type("Choice", (), {"message": message, "finish_reason": "stop"})()
        return type("Response", (), {"choices": [choice]})()

    monkeypatch.setattr(engine, "_create_completion_with_fallback", completion)
    monkeypatch.setattr(
        engine, "exec_tool",
        lambda name, raw_args, **_kwargs: executed.append(name) or "Search results")

    answer = engine.run_agent(
        [{"role": "user", "content": (
            "Plan a Dubai-to-Abu Dhabi business trip. Visit 2 notable attractions "
            "and check current prices. Do not book anything."
        )}],
        max_turns=3, system_prompt="", tools_def=[],
        allowed_tools={"web_search", "browse_page"}, memory_capture=False,
    )

    assert executed == ["web_search"]
    assert "could not verify" in answer
    assert not engine._browser_request_is_explicit([{"role": "user", "content":
        "Visit 2 notable attractions in Abu Dhabi."}])


def test_user_supplied_url_keeps_narrow_browser_task(engine, monkeypatch):
    response = ('✿FUNCTION✿: browse_page ✿ARGS✿: '
                '{"url":"https://example.com/hotel",'
                '"task":"Read only the twin-room price."}')
    message = type("Message", (), {"content": response})()
    choice = type("Choice", (), {"message": message, "finish_reason": "stop"})()
    monkeypatch.setattr(
        engine, "_create_completion_with_fallback",
        lambda *_args, **_kwargs: type("Response", (), {"choices": [choice]})(),
    )
    executed = []
    monkeypatch.setattr(
        engine, "exec_tool",
        lambda _name, raw_args, **_kwargs: executed.append(json.loads(raw_args)) or "done",
    )

    engine.run_agent([
        {"role": "user", "content": (
            "Check https://example.com/hotel for twin rooms, then plan my trip."
        )},
    ], max_turns=1, system_prompt="", tools_def=[],
        allowed_tools={"browse_page"}, memory_capture=False)

    assert executed[0]["task"] == "Read only the twin-room price."


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


def test_a_withheld_tool_is_not_reported_as_nonexistent(engine, monkeypatch):
    """browse_page is gated, not missing, and the wording decides the next move.

    Told the tool "does not exist", a live run went looking for it: search_tools,
    then describe_tool, then refused again -- three turns to learn what the
    first message should have said. The tool is real; it is just not enabled for
    a request that never asked for a browser.
    """
    seen = []

    def completion(*_args, **_kwargs):
        message = type("Message", (), {
            "content": '✿FUNCTION✿: browse_page ✿ARGS✿: {"url": "https://example.com"}'
        })()
        choice = type("Choice", (), {"message": message, "finish_reason": "stop"})()
        return type("Response", (), {"choices": [choice]})()

    monkeypatch.setattr(engine, "_create_completion_with_fallback", completion)
    engine.run_agent(
        [{"role": "user", "content": "What are current bus fares from Dubai to Abu Dhabi?"}],
        max_turns=2, tools_def=[], allowed_tools={"web_search"},
        system_prompt="", memory_capture=False,
        on_result=lambda kind, text: seen.append(f"{kind}:{text}"),
    )

    refusals = [s for s in seen if "browse_page" in s]
    assert refusals, "the refusal has to reach the caller"
    assert not any("does not exist" in s for s in refusals), \
        "browse_page exists; saying otherwise sends the model hunting for it"
    assert any("not available for this request" in s for s in refusals)
