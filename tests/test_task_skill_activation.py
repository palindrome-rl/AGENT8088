def test_plan_mode_injects_the_planning_workflow(engine):
    engine.PERMISSION_MODE = "plan-only"

    prompt = engine.render_task_skill_docs([{"role": "user", "content": "Plan the migration."}])

    assert "### using-agent-skills" in prompt
    assert "### planning-and-task-breakdown" in prompt
    assert "### workspace-engineering" not in prompt


def test_trip_planning_does_not_inject_software_plan_file_instructions(engine):
    prompt = engine.render_task_skill_docs([
        {"role": "user", "content": (
            "Plan a business trip and visit two attractions in Abu Dhabi."
        )},
    ])

    assert "### planning-and-task-breakdown" not in prompt
    assert "tasks/plan.md" not in prompt


def test_code_task_injects_a_workspace_workflow(engine):
    prompt = engine.render_task_skill_docs(
        [{"role": "user", "content": "Implement the queue behavior."}]
    )

    assert "### using-agent-skills" in prompt
    assert "### workspace-engineering" in prompt
    assert "### performance-optimization" not in prompt


def test_agent_loop_receives_the_selected_workflow(engine, monkeypatch):
    seen = []

    def completion(*_args, **kwargs):
        seen.append(kwargs["system_prompt"])
        message = type("Message", (), {"content": "done"})()
        choice = type("Choice", (), {"message": message, "finish_reason": "stop"})()
        return type("Response", (), {"choices": [choice]})()

    monkeypatch.setattr(engine, "_create_completion_with_fallback", completion)

    assert engine.run_agent(
        [{"role": "user", "content": "Implement the queue behavior."}], max_turns=1
    ) == "done"
    assert "### workspace-engineering" in seen[0]


def test_disabled_or_unavailable_skill_is_not_injected(engine):
    engine.set_disabled_skills({"performance-optimization"})
    messages = [{"role": "user", "content": "Profile this slow endpoint."}]

    assert engine.render_task_skill_docs(messages) == ""
    assert "delegation-and-audit" not in engine.select_task_skills(
        [{"role": "user", "content": "Delegate this task."}], available_tools=set()
    )


def test_shared_skill_reference_is_readable_and_confined(engine):
    content = engine.read_skill_resource(
        "using-agent-skills", "shared/definition-of-done.md"
    )

    assert "Definition of Done" in content
    try:
        engine.read_skill_resource("using-agent-skills", "shared/../SKILL.md")
    except ValueError as exc:
        assert "escapes" in str(exc)
    else:
        raise AssertionError("shared references must remain confined")
