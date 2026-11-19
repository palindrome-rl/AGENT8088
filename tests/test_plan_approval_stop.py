"""A refused plan belongs to the next human turn, not another model retry."""


def test_declined_plan_stops_after_one_approval_prompt(engine, monkeypatch, tmp_path):
    # monkeypatch, not assignment: enter_plan_mode moves the module
    # global to plan-only and nothing here moved it back, so the next
    # test in the session composed a plan-only prompt and failed on a
    # tool the mode legitimately hides.
    monkeypatch.setattr(engine, "PERMISSION_MODE", "readonly")
    monkeypatch.setattr(engine, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(engine, "ALLOWED_PATHS", [tmp_path])
    engine.enter_plan_mode()
    shown = []
    monkeypatch.setattr(engine, "_plan_on_approval",
                        lambda plan: shown.append(plan) or "", raising=False)
    completions = []

    def completion(*_args, **_kwargs):
        completions.append(True)
        message = type("Message", (), {
            "content": ('✿FUNCTION✿: present_plan ✿ARGS✿: '
                        '{"plan":"1. Create welcome.txt; 2. Read it back."}')
        })()
        choice = type("Choice", (), {"message": message, "finish_reason": "stop"})()
        return type("Response", (), {"choices": [choice]})()

    monkeypatch.setattr(engine, "_create_completion_with_fallback", completion)
    answer = engine.run_agent(
        [{"role": "user", "content": "Plan a small file write, then wait for approval."}],
        max_turns=5, tools_def=[], allowed_tools={"present_plan"},
        system_prompt="", memory_capture=False,
    )

    assert len(completions) == 1
    assert len(shown) == 1
    assert "not approved" in answer.lower()
    assert "No plan steps were run" in answer
    assert str(tmp_path / "PLAN.md") in answer
    assert (tmp_path / "PLAN.md").exists()
    assert engine.PERMISSION_MODE == "plan-only"


def test_noninteractive_plan_returns_the_plan_instead_of_retrying(engine, monkeypatch):
    # monkeypatch, not assignment: enter_plan_mode moves the module
    # global to plan-only and nothing here moved it back, so the next
    # test in the session composed a plan-only prompt and failed on a
    # tool the mode legitimately hides.
    monkeypatch.setattr(engine, "PERMISSION_MODE", "readonly")
    engine.enter_plan_mode()
    monkeypatch.setattr(engine, "_plan_on_approval", None, raising=False)
    completions = []

    def completion(*_args, **_kwargs):
        completions.append(True)
        message = type("Message", (), {
            "content": ('✿FUNCTION✿: present_plan ✿ARGS✿: '
                        '{"plan":"1. Inspect inputs; 2. Validate result."}')
        })()
        choice = type("Choice", (), {"message": message, "finish_reason": "stop"})()
        return type("Response", (), {"choices": [choice]})()

    monkeypatch.setattr(engine, "_create_completion_with_fallback", completion)
    answer = engine.run_agent(
        [{"role": "user", "content": "Plan this task."}], max_turns=5,
        tools_def=[], allowed_tools={"present_plan"}, system_prompt="",
        memory_capture=False,
    )

    assert len(completions) == 1
    assert "Inspect inputs" in answer
    assert "cannot request approval" in answer
