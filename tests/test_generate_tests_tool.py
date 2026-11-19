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


# --- sheet #34: reasoning markup must never reach a generated test file ---

def _write(engine, monkeypatch, path, content):
    rel = "repo/" + Path(path).relative_to(path.parents[1] if path.parent.name == "tests" else path.parent).as_posix()
    return engine.run_tool("write_file", {"filename": rel, "content": content})


GOOD = "def test_a():\n    assert 1 + 1 == 2\n"


@pytest.mark.parametrize("raw", [
    "<think>plan the tests</think>\n" + GOOD,
    "<think>\nplan\nmore plan\n</think>\n" + GOOD,
    "I will write tests now.\n</think>\n" + GOOD,
    "</think>\n" + GOOD,
    "```python\n" + GOOD + "```",
    GOOD + "<think>\nrunaway thought",
])
def test_wrappers_and_fences_are_stripped_and_the_file_collects(engine, workspace, monkeypatch, raw):
    import ast
    target = workspace / "tests" / "test_detect.py"
    result = _write(engine, monkeypatch, target, raw)
    assert "Error" not in result, result
    text = target.read_text(encoding="utf-8")
    assert "think" not in text and "```" not in text
    ast.parse(text)


def test_tag_text_inside_code_is_kept(engine, workspace, monkeypatch):
    code = ('def test_tag():\n    s = "<think>x</think>"\n'
            '    # </think> appears in this comment\n    assert "think" in s\n')
    target = workspace / "tests" / "test_tags.py"
    _write(engine, monkeypatch, target, code)
    assert target.read_text(encoding="utf-8") == code


def test_invalid_python_does_not_overwrite_a_valid_test_file(engine, workspace, monkeypatch):
    target = workspace / "tests" / "test_detect.py"
    target.parent.mkdir(parents=True)
    target.write_text(GOOD, encoding="utf-8")
    result = _write(engine, monkeypatch, target, "def test_b(:\n    pass\n")
    assert "NOT written" in result and "not valid Python" in result
    assert target.read_text(encoding="utf-8") == GOOD


def test_stripped_but_still_invalid_output_is_refused(engine, workspace, monkeypatch):
    target = workspace / "tests" / "test_new.py"
    result = _write(engine, monkeypatch, target, "<think>x</think>\ndef broken(:\n")
    assert "NOT written" in result and not target.exists()


def test_generate_tests_flags_a_broken_generated_file(engine, workspace, monkeypatch):
    (workspace / "parser.py").write_text("def parse(s):\n    return s\n", encoding="utf-8")
    bad = workspace / "tests" / "test_parser.py"

    def fake_subagent(args, depth=0):
        bad.parent.mkdir(exist_ok=True)
        bad.write_text("</think>\nnot python (\n", encoding="utf-8")
        engine._TURN_FILES_TOUCHED.append(str(bad))
        return "Wrote tests"

    monkeypatch.setattr(engine, "_exec_subagent", fake_subagent)
    result = engine.run_tool("generate_tests", {"filename": "repo/parser.py"})
    assert "not valid Python" in result and "test_parser.py" in result
    # Cleaned output collects under pytest too.
    import subprocess, sys
    ok = workspace / "tests" / "test_ok.py"
    _write(engine, monkeypatch, ok, "</think>\n" + GOOD)
    run = subprocess.run([sys.executable, "-m", "pytest", "-q", str(ok), "-p", "no:cacheprovider"],
                         capture_output=True, text=True, cwd=str(workspace))
    assert "1 passed" in run.stdout, run.stdout
