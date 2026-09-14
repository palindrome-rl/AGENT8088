def test_repository_reading_skill_is_progressive_and_requires_the_tool(engine):
    skill = engine.SKILL_PACKAGES["repository-reading"]
    assert skill["progressive"] is True
    assert skill["requires_tools"] == {"repository_read"}
    assert "overview" in engine.read_skill_resource("repository-reading", "SKILL.md")


def test_repository_reading_skill_activates_for_github_investigation(engine):
    names = engine.select_task_skills([
        {"role": "user", "content": "Inspect https://github.com/example/project and find the auth flow."},
    ])
    assert "repository-reading" in names


def test_repository_reading_skill_stays_hidden_without_repository_tool(engine):
    names = engine.select_task_skills([
        {"role": "user", "content": "Read a GitHub repository."},
    ], available_tools=set(engine.TOOL_NAMES) - {"repository_read"})
    assert "repository-reading" not in names
