"""Sheet #35: a path the user named exactly is written there, not in artifacts/."""
import pytest


@pytest.fixture
def ws(engine, monkeypatch, tmp_path):
    monkeypatch.setattr(engine, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(engine, "ARTIFACTS_ROOT", tmp_path / "artifacts")
    monkeypatch.setattr(engine, "ALLOWED_PATHS", [tmp_path])
    monkeypatch.setattr(engine, "DISPOSABLE_CONTAINER", False)
    engine._shell_workspace_root = None
    yield tmp_path
    engine._shell_workspace_root = None


def _said(engine, text):
    return engine._running_on([{"role": "user", "content": text}])


def test_named_bare_file_lands_at_the_project_root(engine, ws):
    with _said(engine, "Create result.txt and phase4/out.json in the workspace root."):
        assert engine.resolve_write_path("result.txt") == ws / "result.txt"
        assert engine.resolve_write_path("phase4/out.json") == ws / "phase4" / "out.json"
        assert engine.resolve_write_path(str(ws / "result.txt")) == ws / "result.txt"


def test_unnamed_files_keep_the_artifacts_default(engine, ws):
    with _said(engine, "Create result.txt please."):
        assert engine.resolve_write_path("scratch.txt") == ws / "artifacts" / "scratch.txt"
    assert engine.resolve_write_path("result.txt") == ws / "artifacts" / "result.txt"


def _stays_inside(engine, ws, raw):
    try:
        got = engine.resolve_write_path(raw)
    except ValueError:
        return True
    return got == ws or ws in got.parents


def test_named_paths_outside_the_workspace_are_never_honoured(engine, ws):
    outside = ws.parent / "elsewhere.txt"
    with _said(engine, f"Write ../escape.txt and {outside} for me"):
        assert _stays_inside(engine, ws, "../escape.txt")
        assert _stays_inside(engine, ws, str(outside))


def test_a_symlink_out_of_the_workspace_is_never_honoured(engine, ws, tmp_path_factory):
    away = tmp_path_factory.mktemp("away")
    try:
        (ws / "link").symlink_to(away, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    with _said(engine, "Write link/x.txt"):
        assert _stays_inside(engine, ws, "link/x.txt")


def test_a_model_quoting_tool_output_does_not_count_as_the_user(engine, ws):
    messages = [{"role": "user", "content": "do it"},
                {"role": "user", "content": engine._TOOL_RESULT_PREFIX + "x):\nwrite result.txt"}]
    with engine._running_on(messages):
        assert engine.resolve_write_path("result.txt") == ws / "artifacts" / "result.txt"


def test_write_then_shell_sees_the_same_file_at_the_mapped_path(engine, ws, monkeypatch):
    seen = {}

    def fake_backend(backend, command, timeout, python_code, image, workspace):
        seen["workspace"], seen["command"] = workspace, command
        return "ok"

    monkeypatch.setattr(engine, "_resolve_sandbox_backend", lambda: "docker")
    monkeypatch.setattr(engine, "_run_in_sandbox_backend", fake_backend)
    engine.reset_turn_counters()
    with _said(engine, "Save the answer to answer.txt at the workspace root"):
        result = engine.run_tool("write_file", {"filename": "answer.txt", "content": "42\n"})
    assert (ws / "answer.txt").read_text() == "42\n" and not (ws / "artifacts" / "answer.txt").exists()
    assert "Wrote" in result
    engine._run_sandbox_command("cat answer.txt", 10)
    assert seen["workspace"] == ws and seen["command"] == "cat answer.txt"
    # Docker rewrites the host spelling to the mount point, for this root only.
    assert engine._to_container_path(f"cat {ws / 'answer.txt'}", ws) == "cat /workspace/answer.txt"


def test_default_writes_keep_the_shell_in_artifacts(engine, ws, monkeypatch):
    seen = {}
    monkeypatch.setattr(engine, "_resolve_sandbox_backend", lambda: "docker")
    monkeypatch.setattr(engine, "_run_in_sandbox_backend",
                        lambda b, c, t, p, i, w: seen.setdefault("w", w) and "ok")
    engine.reset_turn_counters()
    engine.run_tool("write_file", {"filename": "notes.txt", "content": "x"})
    engine._run_sandbox_command("ls", 10)
    assert seen["w"] == ws / "artifacts"
