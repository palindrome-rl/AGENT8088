"""The build-test-fix procedure points at the skills Agent8088 already ships, and delegation has rules.

The system prompt listed every installed skill, but nothing said which one fits which moment, so a small model rarely
loaded one. A short procedure block now names them by situation (only the ones installed), the note for a check that
keeps failing the same way names the debugging skill, and the sub-agent tool says when to delegate and what to do when
a limit refuses a spawn.
"""
from pathlib import Path

from agent8088.trajectory import TrajectoryState

PROCEDURE_SKILLS = ("incremental-implementation", "test-driven-development", "debugging-and-error-recovery",
                    "browser-qa")


def _skills(*names):
    return {name: {"name": name, "description": f"{name} description"} for name in names}


def test_the_procedure_names_the_installed_skills_by_situation(engine):
    text = engine.render_skill_docs(_skills(*PROCEDURE_SKILLS, "humanizer"))
    block = text.split("## Build, test, fix", 1)[1]
    assert "runnable" in block and "incremental-implementation" in block
    assert "test-driven-development" in block and "debugging-and-error-recovery" in block and "browser-qa" in block
    assert "humanizer" not in block


def test_a_skill_that_is_not_installed_is_not_named(engine):
    text = engine.render_skill_docs(_skills("browser-qa", "humanizer"))
    block = text.split("## Build, test, fix", 1)[1]
    assert "browser-qa" in block and "test-driven-development" not in block


def test_no_procedure_skills_means_no_block(engine):
    assert "## Build, test, fix" not in engine.render_skill_docs(_skills("humanizer"))
    assert engine.render_skill_docs({}) == ""


def test_the_repeat_note_points_at_the_debugging_skill(engine, monkeypatch, tmp_path):
    monkeypatch.setattr(engine, "SKILL_PACKAGES", _skills("debugging-and-error-recovery"))
    state = TrajectoryState({}, goal="fix it", message_count=1, workspace=str(tmp_path))
    fail = "FAILED tests/test_a.py::test_x - assert 1 == 2\nCommand exited with status 1."
    for edit in (False, True):
        if edit:
            op = state.before_tool("edit_file")
            state.after_tool(op, "ok", failed=False, blocked=False, mutated=True, path=str(tmp_path / "a.py"))
        op = state.before_tool("execute_shell")
        state.after_tool(op, fail, failed=True, blocked=False, mutated=False, verdict="failed",
                         command="python -m pytest -q")
    assert "debugging-and-error-recovery" in engine._repeat_failure_note(state)


def test_the_subagent_tool_carries_the_delegation_rules():
    from agent8088 import engine as e
    line = next(l for l in (Path(e.__file__).parent / "tools.txt").read_text(encoding="utf-8").splitlines()
                if l.startswith("spawn_subagent|"))
    description = line.split("|")[1]
    assert "independent" in description and "do not retry" in description.lower()
    assert "your own work" in description
