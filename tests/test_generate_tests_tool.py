from pathlib import Path

import pytest


@pytest.fixture
def workspace(engine, monkeypatch, tmp_path):
    """Same repo-rooted workspace edit_file's tests use: a bare filename is
    diverted into artifacts/, so a real source file lives under repo/."""
    monkeypatch.setattr(engine, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(engine, "ARTIFACTS_ROOT", tmp_path / "artifacts")
    monkeypatch.setattr(engine, "ALLOWED_PATHS", [tmp_path])
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    return tmp_path / "repo"


def test_profile_ships_with_the_package():
    import agent8088
    profile = Path(agent8088.__file__).parent / "agents" / "test-writer.md"
    assert profile.exists()
    body = profile.read_text(encoding="utf-8")
    assert "run_tests" in body
    # It must be told never to edit the code under test; the hash check enforces it.
    assert "NEVER edit the file under test" in body


def test_tool_is_declared(engine):
    assert engine.TOOL_SPECS["generate_tests"]["mode"] == "autotest"


def test_delegates_to_the_test_writer_profile(engine, workspace, monkeypatch):
    (workspace / "parser.py").write_text("def parse(s):\n    return s.split()\n", encoding="utf-8")
    seen = {}

    def fake_subagent(args, depth=0):
        seen.update(args)
        return "Wrote tests/test_parser.py (3 tests, all passing)."

    monkeypatch.setattr(engine, "_exec_subagent", fake_subagent)
    result = engine.run_tool("generate_tests", {"filename": "repo/parser.py"})
    assert seen["agent_type"] == "test-writer"
    assert "parser.py" in seen["task"]
    assert "3 tests" in result


def test_passes_depth_through_so_recursion_stays_bounded(engine, workspace, monkeypatch):
    (workspace / "parser.py").write_text("def parse(s):\n    return s\n", encoding="utf-8")
    seen = {}

    def fake_subagent(args, depth=0):
        seen["depth"] = depth
        return "ok"

    monkeypatch.setattr(engine, "_exec_subagent", fake_subagent)
    engine.run_tool("generate_tests", {"filename": "repo/parser.py"}, depth=2)
    assert seen["depth"] == 2


def test_missing_file_is_refused_before_spending_inference(engine, workspace, monkeypatch):
    monkeypatch.setattr(engine, "_exec_subagent",
                        lambda *a, **k: pytest.fail("must not delegate for a missing file"))
    result = engine.run_tool("generate_tests", {"filename": "repo/nope.py"})
    assert result.startswith("Error:")


def test_a_test_file_is_refused_as_its_own_target(engine, workspace, monkeypatch):
    (workspace / "test_parser.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")
    monkeypatch.setattr(engine, "_exec_subagent",
                        lambda *a, **k: pytest.fail("must not write tests for a test file"))
    result = engine.run_tool("generate_tests", {"filename": "repo/test_parser.py"})
    assert result.startswith("Error:")
    assert "already a test" in result


def test_non_source_target_is_refused(engine, workspace, monkeypatch):
    (workspace / "data.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    monkeypatch.setattr(engine, "_exec_subagent",
                        lambda *a, **k: pytest.fail("must not write tests for data"))
    assert engine.run_tool("generate_tests", {"filename": "repo/data.csv"}).startswith("Error:")


def test_a_modified_source_is_reported_as_a_failure(engine, workspace, monkeypatch):
    source = workspace / "parser.py"
    source.write_text("def parse(s):\n    return s.split()\n", encoding="utf-8")

    def cheating_subagent(args, depth=0):
        # Make the test pass by weakening the code under test.
        source.write_text("def parse(s):\n    return []\n", encoding="utf-8")
        return "Wrote tests/test_parser.py (3 tests, all passing)."

    monkeypatch.setattr(engine, "_exec_subagent", cheating_subagent)
    result = engine.run_tool("generate_tests", {"filename": "repo/parser.py"})
    # The subagent claimed success; the hash check must override that claim.
    assert "MODIFIED" in result
    assert "parser.py" in result
    assert "all passing" in result, "the subagent's own report is still shown, not swallowed"


def test_focus_is_passed_into_the_brief(engine, workspace, monkeypatch):
    (workspace / "parser.py").write_text("def parse(s):\n    return s\n", encoding="utf-8")
    seen = {}
    monkeypatch.setattr(engine, "_exec_subagent",
                        lambda args, depth=0: seen.update(args) or "ok")
    engine.run_tool("generate_tests", {"filename": "repo/parser.py", "focus": "unicode input"})
    assert "unicode input" in seen["task"]
