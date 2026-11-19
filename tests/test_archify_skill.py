from pathlib import Path


def test_archify_skill_is_progressive_and_bundled(engine):
    skill = engine.SKILL_PACKAGES["archify"]
    assert skill["progressive"] is True
    assert skill["version"] == "2.17"

    prompt = engine.render_skill_docs({"archify": skill})
    assert "view_skill" in prompt
    assert "Fast authoring path" not in prompt

    root = Path(skill["path"])
    assert (root / "bin" / "archify.mjs").is_file()
    assert "Create a self-contained" in engine.read_skill_resource(
        "archify", "SKILL.md"
    )


def test_archify_skill_activates_for_diagram_requests(engine):
    names = engine.select_task_skills(
        [{"role": "user", "content": "Create an architecture diagram."}]
    )
    assert "archify" in names
