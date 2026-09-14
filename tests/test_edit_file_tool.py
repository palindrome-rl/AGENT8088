import pytest


@pytest.fixture
def workspace(engine, monkeypatch, tmp_path):
    """A writable project file inside a repository.

    Writes are workspace-restricted AND a bare filename is diverted into
    artifacts/ -- only a path inside a real repository writes through. So the
    fixture makes repo/ a git repository and every test edits repo/<file>,
    which is what editing a project's own file actually looks like.
    """
    monkeypatch.setattr(engine, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(engine, "ARTIFACTS_ROOT", tmp_path / "artifacts")
    monkeypatch.setattr(engine, "ALLOWED_PATHS", [tmp_path])
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    engine.reset_turn_counters()
    return tmp_path / "repo"


def test_tool_is_declared_with_the_shared_write_mode(engine):
    spec = engine.TOOL_SPECS["edit_file"]
    # Sharing write_text is the whole safety argument; a new mode would skip guards.
    assert spec["mode"] == "write_text"
    assert spec["path_arg"] == "filename"
    assert spec["content_arg"] == "new_string"


def test_schema_declares_its_three_arguments(engine):
    schema = engine.build_tools_def({"edit_file": engine.TOOL_SPECS["edit_file"]})[0]
    params = schema["function"]["parameters"]
    assert set(params["properties"]) == {"filename", "old_string", "new_string"}
    assert params["required"] == ["filename", "old_string"]


def test_applies_a_unique_edit_and_reports_the_counts(engine, workspace):
    target = workspace / "app.py"
    target.write_text("def run(x):\n    return x\n", encoding="utf-8")
    result = engine.run_tool("edit_file", {
        "filename": "repo/app.py", "old_string": "    return x", "new_string": "    return x * 2"})
    assert "1 addition" in result and "1 removal" in result
    assert target.read_text(encoding="utf-8") == "def run(x):\n    return x * 2\n"


def test_a_refused_edit_writes_nothing_and_spends_no_write_budget(engine, workspace):
    target = workspace / "app.py"
    original = "x = 1\ny = 2\nx = 1\n"
    target.write_text(original, encoding="utf-8")
    result = engine.run_tool("edit_file", {
        "filename": "repo/app.py", "old_string": "x = 1", "new_string": "x = 3"})
    assert result.startswith("Error:")
    assert "2 times" in result
    assert target.read_text(encoding="utf-8") == original
    # A refusal is not a write; it must not consume max_writes_per_turn.
    assert engine._turn_writes == 0


def test_missing_file_points_at_write_file(engine, workspace):
    result = engine.run_tool("edit_file", {
        "filename": "repo/nope.py", "old_string": "a", "new_string": "b"})
    assert result.startswith("Error:")
    assert "write_file" in result
    assert engine._turn_writes == 0


def test_records_a_diff_for_the_renderers(engine, workspace):
    (workspace / "app.py").write_text("a\nb\nc\n", encoding="utf-8")
    engine.run_tool("edit_file", {"filename": "repo/app.py", "old_string": "b", "new_string": "B"})
    assert engine._last_write_diff, "the CLI and web renderers both read _last_write_diff"
    assert any(line.startswith("+B") for line in engine._last_write_diff)


def test_the_hunk_comes_back_with_the_result(engine, workspace):
    (workspace / "app.py").write_text("a\nb\nc\n", encoding="utf-8")
    result = engine.run_tool("edit_file", {
        "filename": "repo/app.py", "old_string": "b", "new_string": "B"})
    # The model should not need a re-read to see what landed.
    assert "+B" in result and "-b" in result


def test_sensitive_path_is_refused_through_the_shared_guard(engine, workspace, monkeypatch):
    target = workspace / "secret.env"
    target.write_text("TOKEN=abc\n", encoding="utf-8")
    monkeypatch.setattr(engine, "_is_sensitive_path", lambda path: True)
    result = engine.run_tool("edit_file", {
        "filename": "repo/secret.env", "old_string": "TOKEN=abc", "new_string": "TOKEN=stolen"})
    assert "sensitive file denied" in result
    assert target.read_text(encoding="utf-8") == "TOKEN=abc\n"


def test_normalized_match_says_so_in_the_result(engine, workspace):
    (workspace / "app.py").write_text("a\n    return x\nc\n", encoding="utf-8")
    result = engine.run_tool("edit_file", {
        "filename": "repo/app.py", "old_string": "    return x   ", "new_string": "    return y"})
    assert "normalising" in result
    assert (workspace / "app.py").read_text(encoding="utf-8") == "a\n    return y\nc\n"
