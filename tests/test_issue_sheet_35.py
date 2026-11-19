"""Issue S35: a deliverable the sandbox moved must be mirrored to the path the user named.

Writes to a bare or invented path land in artifacts/, so the path the person asked
for is empty and a grader looking there finds nothing. After the run the file is
copied back to that path, but only when the person's own message names it, never
over an existing file, and a failed copy degrades to the workspace disclosure.
"""
import pytest

from agent8088 import trajectory as trajectory_mod


@pytest.fixture
def ws(engine, monkeypatch, tmp_path):
    project, artifacts = tmp_path / "proj", tmp_path / "proj" / "artifacts"
    artifacts.mkdir(parents=True)
    monkeypatch.setattr(engine, "PROJECT_ROOT", project)
    monkeypatch.setattr(engine, "ARTIFACTS_ROOT", artifacts)
    monkeypatch.setattr(engine, "ALLOWED_PATHS", [tmp_path])
    monkeypatch.setattr(engine, "DISPOSABLE_CONTAINER", False)
    monkeypatch.setattr(engine, "_resolve_sandbox_backend", lambda: "docker")
    engine._shell_workspace_root = None
    return project, artifacts


def _said(engine, text):
    return engine._running_on([{"role": "user", "content": text}])


def _landed_write(traj, requested, artifacts_file, content="answer\n"):
    """A write the sandbox diverted: the file is on disk in artifacts/, and the
    trajectory is told both what was asked for and where it landed."""
    artifacts_file.parent.mkdir(parents=True, exist_ok=True)
    artifacts_file.write_text(content, encoding="utf-8")
    operation = traj.before_tool("write_file")
    traj.after_tool(operation, "Wrote file", failed=False, blocked=False, mutated=True,
                    path=requested, written_to=str(artifacts_file))


def test_a_named_path_the_sandbox_diverted_is_mirrored(engine, ws):
    project, artifacts = ws
    traj = trajectory_mod.TrajectoryState(None, goal="g")
    with _said(engine, f"Save the answer to {project.resolve()}/solution.txt"):
        assert engine.resolve_write_path("solution.txt") == artifacts / "solution.txt"
        _landed_write(traj, "solution.txt", artifacts / "solution.txt")
        answer = engine._with_task_notes("Done.", traj)
    assert (project / "solution.txt").read_text(encoding="utf-8") == "answer\n"
    assert (artifacts / "solution.txt").exists(), "the artifacts copy must be kept"
    assert "Also copied to the path you named" in answer


def test_an_existing_user_file_is_never_clobbered(engine, ws):
    project, artifacts = ws
    (project / "solution.txt").write_text("the user's own text", encoding="utf-8")
    traj = trajectory_mod.TrajectoryState(None, goal="g")
    with _said(engine, f"Save the answer to {project.resolve()}/solution.txt"):
        _landed_write(traj, "solution.txt", artifacts / "solution.txt")
        engine._with_task_notes("Done.", traj)
    assert (project / "solution.txt").read_text(encoding="utf-8") == "the user's own text"


def test_an_unnamed_path_is_not_mirrored(engine, ws):
    project, artifacts = ws
    traj = trajectory_mod.TrajectoryState(None, goal="g")
    with _said(engine, "Write me a short summary of the design."):
        _landed_write(traj, "notes.txt", artifacts / "notes.txt")
        answer = engine._with_task_notes("Done.", traj)
    assert not (project / "notes.txt").exists()
    assert "Also copied" not in answer


def test_a_failed_mirror_degrades_to_the_disclosure(engine, ws, monkeypatch):
    project, artifacts = ws
    traj = trajectory_mod.TrajectoryState(None, goal="g")

    def broken_copy(src, dst, *args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(engine.shutil, "copyfileobj", broken_copy)
    with _said(engine, f"Save the answer to {project.resolve()}/solution.txt"):
        _landed_write(traj, "solution.txt", artifacts / "solution.txt")
        answer = engine._with_task_notes("Done.", traj)
    assert not (project / "solution.txt").exists(), "no partial copy may be left behind"
    assert (artifacts / "solution.txt").exists()
    assert "Could not copy to the path you named" in answer
    assert str(artifacts) in answer, "the existing disclosure still names the real location"
