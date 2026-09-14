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

    monkeypatch.setattr(engine, "_exec_shell_command", fake_shell)
    engine.run_tool("run_tests", {"path": str(tmp_path)})
    assert seen["command"] == "pytest -q"


def test_undiscoverable_project_reports_before_running_anything(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "_exec_shell_command",
                        lambda *a, **k: pytest.fail("must not run a command it could not discover"))
    result = engine.run_tool("run_tests", {"path": str(tmp_path)})
    assert result.startswith("Error:")
