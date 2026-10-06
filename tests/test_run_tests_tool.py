import pytest

from agent8088 import testing_support


def test_pytest_from_pyproject(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    found = testing_support.discover_command(str(tmp_path))
    assert found.framework == "pytest"
    assert found.command == "pytest -q"
    assert found.error is None


def test_pytest_from_a_tests_directory_alone(tmp_path):
    (tmp_path / "tests").mkdir()
    assert testing_support.discover_command(str(tmp_path)).framework == "pytest"


def test_npm_when_package_json_declares_a_test_script(tmp_path):
    (tmp_path / "package.json").write_text('{"scripts":{"test":"vitest run"}}', encoding="utf-8")
    found = testing_support.discover_command(str(tmp_path))
    assert found.framework == "npm"
    assert found.command == "npm test --silent"


def test_package_json_without_a_test_script_is_not_npm(tmp_path):
    (tmp_path / "package.json").write_text('{"scripts":{"build":"vite build"}}', encoding="utf-8")
    assert testing_support.discover_command(str(tmp_path)).error is not None


def test_malformed_package_json_does_not_raise(tmp_path):
    (tmp_path / "package.json").write_text("{not json", encoding="utf-8")
    assert testing_support.discover_command(str(tmp_path)).error is not None


@pytest.mark.parametrize("marker,contents,framework,command", [
    ("go.mod", "module x\n", "go", "go test ./..."),
    ("Cargo.toml", "[package]\nname='x'\n", "cargo", "cargo test"),
])
def test_other_ecosystems(tmp_path, marker, contents, framework, command):
    (tmp_path / marker).write_text(contents, encoding="utf-8")
    found = testing_support.discover_command(str(tmp_path))
    assert (found.framework, found.command) == (framework, command)


def test_makefile_needs_a_real_test_target(tmp_path):
    (tmp_path / "Makefile").write_text("build:\n\tcc x.c\n", encoding="utf-8")
    assert testing_support.discover_command(str(tmp_path)).error is not None
    (tmp_path / "Makefile").write_text("test:\n\tpytest\n", encoding="utf-8")
    assert testing_support.discover_command(str(tmp_path)).command == "make test"


def test_python_wins_over_make_when_both_are_present(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (tmp_path / "Makefile").write_text("test:\n\tpytest\n", encoding="utf-8")
    # Order is part of the contract: the language's own runner beats a wrapper.
    assert testing_support.discover_command(str(tmp_path)).framework == "pytest"


def test_no_marker_names_what_it_looked_for(tmp_path):
    found = testing_support.discover_command(str(tmp_path))
    assert found.error.startswith("Error:")
    for marker in ("pyproject.toml", "package.json", "go.mod", "Cargo.toml", "Makefile"):
        assert marker in found.error


def test_missing_directory_is_an_error_not_a_crash():
    assert testing_support.discover_command("/no/such/dir").error is not None


def test_trim_output_keeps_the_head_and_says_what_it_cut():
    text = "\n".join(f"line {i}" for i in range(500))
    trimmed = testing_support.trim_output(text, max_chars=200)
    assert len(trimmed) < 400
    assert "line 0" in trimmed
    assert "truncated" in trimmed


def test_trim_output_leaves_short_output_alone():
    assert testing_support.trim_output("2 passed") == "2 passed"


def test_tool_is_declared_as_a_shell_tool(engine):
    # Sharing mode=shell is what gives it approval, sandbox and timeout handling.
    assert engine.TOOL_SPECS["run_tests"]["mode"] == "shell"


def test_engine_injects_the_discovered_command(engine, tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    seen = {}

    def fake_shell(command, **kwargs):
        seen["command"] = command
        return "1 passed"

    monkeypatch.setattr(engine, "_resolve_sandbox_backend", lambda: "native")
    engine.PERMISSION_MODE = "full-auto"
    monkeypatch.setattr(engine, "_exec_shell_command", fake_shell)
    engine.run_tool("run_tests", {"path": str(tmp_path)})
    assert seen["command"] == "pytest -q"


def test_undiscoverable_project_reports_before_running_anything(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "_exec_shell_command",
                        lambda *a, **k: pytest.fail("must not run a command it could not discover"))
    result = engine.run_tool("run_tests", {"path": str(tmp_path)})
    assert result.startswith("Error:")


# --- discovery walks up ------------------------------------------------------
# Found in live testing: the test-writer sub-agent passed the directory the
# tests live in (proj/tests), not the project root, and got "no test command
# could be discovered" even though proj/pyproject.toml was one level up. A
# caller naming any directory inside the project must reach the project's
# runner.

def test_discovery_walks_up_to_the_project_root(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    deep = tmp_path / "pkg" / "tests"
    deep.mkdir(parents=True)
    found = testing_support.discover_command(str(deep))
    assert found.error is None
    assert found.command == "pytest -q"


def test_walking_up_prefers_the_nearest_marker(tmp_path):
    (tmp_path / "go.mod").write_text("module outer\n", encoding="utf-8")
    inner = tmp_path / "web"
    inner.mkdir()
    (inner / "package.json").write_text('{"scripts":{"test":"vitest run"}}', encoding="utf-8")
    # Standing in the inner project, the inner project's runner wins.
    assert testing_support.discover_command(str(inner)).framework == "npm"


def test_walking_up_stops_and_reports_when_nothing_is_found(tmp_path):
    deep = tmp_path / "a" / "b" / "c"
    deep.mkdir(parents=True)
    found = testing_support.discover_command(str(deep))
    assert found.error is not None
    assert "pyproject.toml" in found.error


def test_walk_up_does_not_escape_past_a_filesystem_root():
    # Must terminate rather than loop at '/'.
    found = testing_support.discover_command("/")
    assert found.command or found.error
