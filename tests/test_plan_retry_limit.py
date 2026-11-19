"""Plan mode gives a small model more than two tries to stop writing, and says what to do when it gives up."""


def test_default_limit_is_four(engine):
    assert engine.PLAN_MODE_RETRY_LIMIT == 4


def test_stop_message_says_what_to_type_next(engine):
    text = engine._plan_retry_limit_message(4)

    assert "stopped after 4 invalid mutation attempts" in text
    assert "Nothing was written or run" in text
    assert "present the plan now" in text
    assert "/mode full-auto" in text


def test_run_agent_uses_the_message_at_the_limit(engine, monkeypatch):
    monkeypatch.setattr(engine, "PERMISSION_MODE", "readonly")
    engine.enter_plan_mode()
    monkeypatch.setattr(engine, "PLAN_MODE_RETRY_LIMIT", 2)

    def completion(*_args, **_kwargs):
        message = type("Message", (), {
            "content": ('\u273fFUNCTION\u273f: write_file \u273fARGS\u273f: '
                        '{"filename":"x.txt","content":"hi"}')
        })()
        choice = type("Choice", (), {"message": message, "finish_reason": "stop"})()
        return type("Response", (), {"choices": [choice]})()

    monkeypatch.setattr(engine, "_create_completion_with_fallback", completion)
    answer = engine.run_agent(
        [{"role": "user", "content": "Build a small thing."}],
        max_turns=8, tools_def=[], allowed_tools={"present_plan"},
        system_prompt="", memory_capture=False,
    )

    assert "stopped after 2 invalid mutation attempts" in answer
    assert "present the plan now" in answer
