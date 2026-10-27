"""A check that failed because of how it was written is settled by the edited command running clean.

Seen in a real run: the check ended `node --check server.py 2>&1 | head -1`. `head` does not exist on Windows (exit 255) and
`server.py` is Python. The model fixed the tail (`python -c "import ast; ..."`) and the edited command ran clean, but it was
not the same command and printed no counted summary, so the run still ended "NOT VERIFIED: the latest check failed" over a
final answer saying the check passed."""
import subprocess
import sys

import pytest

from agent8088.trajectory import TrajectoryState, _revised_command

WS = "C:\\w\\proj"
FAILED = (f'cd "{WS}" && echo "=== FILES ===" && dir /b && echo "=== JS SYNTAX CHECK ===" && node --check app.js '
          '&& echo "app.js OK" && node --check server.py 2>&1 | head -1')
FIXED = (f'cd "{WS}" && echo "=== FILES ===" && dir /b && echo "=== JS SYNTAX CHECK ===" && node --check app.js '
         '&& echo "app.js OK" && echo "=== PY SYNTAX CHECK ===" && python -c "import ast; ast.parse(open(\'server.py\').read())"')


def _run(state, command, result, verdict="unknown"):
    operation = state.before_tool("execute_shell")
    state.after_tool(operation, result, failed=False, blocked=False, mutated=False, verdict=verdict, command=command)


def _failed_state():
    state = TrajectoryState({}, goal="g", message_count=1)
    _run(state, FAILED, "'head' is not recognized as an internal or external command\nCommand exited with status 255.",
         verdict="failed")
    assert state.check_failing()
    return state


def test_the_edited_check_running_clean_settles_the_failure():
    state = _failed_state()
    _run(state, FIXED, "app.js OK\nserver.py OK")
    assert not state.check_failing()


def test_the_edited_check_failing_again_keeps_the_failure():
    state = _failed_state()
    _run(state, FIXED, "SyntaxError: invalid syntax\nCommand exited with status 1.", verdict="failed")
    assert state.check_failing()


def test_an_unrelated_check_does_not_settle_it():
    state = _failed_state()
    _run(state, "npm run lint", "done")
    assert not _revised_command(FAILED, "npm run lint")
    assert state.check_failing()


def test_a_listing_is_not_a_check():
    state = _failed_state()
    _run(state, f'cd "{WS}" && dir /b', "app.js\nserver.py")
    assert state.check_failing()


def test_an_edited_command_that_prints_a_traceback_keeps_the_failure():
    state = _failed_state()
    _run(state, FIXED, "Traceback (most recent call last):\n  File x\nValueError: bad")
    assert state.check_failing()


def test_a_short_command_is_not_matched_loosely():
    assert not _revised_command("pytest", "pytest -k other")


def test_dropping_the_failed_clause_is_not_an_edit_of_it():
    assert not _revised_command("pytest tests/test_a.py & pytest tests/test_b.py", "pytest tests/test_a.py")


@pytest.mark.parametrize("failed, rerun", [
    ("node --check broken.js 2>&1", "node --check broken.js 2>&1 || echo recovered"),
    ("pytest tests/test_bad.py --verbose --capture=no", "pytest tests/test_bad.py --verbose --capture=no -k unrelated"),
    ("node --check good.js && node --check broken.js", "node --check good.js && echo broken.js checked"),
    ("node --check good.js && node --check broken.js", "node --check good.js && node --check good.js && echo broken.js"),
    ("node --check broken.js", "node --check broken.js ; echo done"),
    ("node --check broken.js", "node --check broken.js & echo done"),
    ("node --check broken.js", "node --check broken.js | echo done"),
    ("node --check broken.js", "node --check broken.js &&"),
    ("node --check Broken.js", "node --check broken.js && echo done"),
    ("node --check broken.js", "node --check other.js && echo broken.js"),
    ("cd first && node --check broken.js", "cd second && node --check broken.js"),
    ("cd first && node --check broken.js", "node --check broken.js"),
    ("pytest tests/test_bad.py --verbose --capture=no", "pytest tests/test_bad.py --verbose --capture=no --version"),
    ("node --check server.py", 'python -c "import ast; ast.parse(open(\'other.py\').read())"'),
    ("node --check server.py", 'python -c "import ast; False and ast.parse(open(\'server.py\').read())"'),
    ("node --check server.py", 'python -c "import ast; ast.parse = print; ast.parse(open(\'server.py\').read())"'),
    ("node --check server.py", 'python -c "import ast; exit(0); ast.parse(open(\'server.py\').read())"'),
    ("node --check server.py", 'python -c "import ast; ast.parse(open(\'server.py\').read()); print(1)"'),
    ("node --check server.py", 'python -c "print(\'ast.parse(open(server.py).read()) check\')"'),
    ("node --check server.py", 'python -c "import ast; ast.parse(open(\'server.py\').read())" || echo done'),
    ("node --check server.py", 'python -c "import ast; ast.parse(open(\'server.py\').read())" | head -1'),
    ("node --check server.py", 'python -c "import ast; ast.parse(open(\'server.py\').read())" # ignored'),
    ("node --check server.py", "python -m py_compile $TARGET.py"),
    ("node --check server.py", "python -m py_compile %TARGET%.py"),
    ("python -m py_compile server.py", 'python -c "import ast; ast.parse(open(\'server.py\').read())"'),
    ("node --check server.py", "python -m py_compile 'server.py'"),
])
def test_ambiguous_or_weaker_retries_leave_the_failed_check_visible(failed, rerun):
    assert not _revised_command(failed, rerun)
    state = TrajectoryState({}, goal="g", message_count=1)
    _run(state, failed, "Command exited with status 1.", verdict="failed")
    assert state.check_failing()
    _run(state, rerun, "done")
    assert state.check_failing()


@pytest.mark.parametrize("failed, rerun", [
    ("node --check server.py", "python -m py_compile server.py"),
    ("node --check server.py | head -1", "python -m py_compile server.py"),
    ('node --check "space dir/server.py"', 'python -m py_compile "space dir/server.py"'),
    (r'cd /d "C:\space dir" && node --check server.py', r'cd "C:\space dir" && python -m py_compile server.py'),
    ("node --check a.py && node --check b.py", "python -m py_compile a.py b.py"),
    ("pytest tests/test_bad.py && node --check server.py", "pytest tests/test_bad.py && python -m py_compile server.py"),
])
def test_supported_syntax_retries_retain_all_targets(failed, rerun):
    assert _revised_command(failed, rerun)
    state = TrajectoryState({}, goal="g", message_count=1)
    _run(state, failed, "Command exited with status 1.", verdict="failed")
    _run(state, rerun, "")
    assert not state.check_failing()


def test_real_python_syntax_check_clears_only_after_the_target_is_fixed(tmp_path):
    target = tmp_path / "server.py"
    target.write_text("def broken(:\n", encoding="utf-8")
    state = TrajectoryState({}, goal="g", message_count=1)
    _run(state, "node --check server.py | head -1", "Command exited with status 255.", verdict="failed")
    code = "import ast; ast.parse(open('server.py').read())"
    retry = 'python -c "' + code + '"'
    bad = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, capture_output=True, text=True, timeout=10, check=False)
    assert bad.returncode != 0
    _run(state, retry, bad.stdout + bad.stderr, verdict="failed")
    assert state.check_failing()
    target.write_text("print('ready')\n", encoding="utf-8")
    good = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, capture_output=True, text=True, timeout=10, check=False)
    assert good.returncode == 0 and good.stdout == "" and good.stderr == ""
    _run(state, retry, good.stdout + good.stderr)
    assert not state.check_failing()


def test_a_real_compile_failure_is_not_settled_by_a_weaker_ast_parse(tmp_path):
    (tmp_path / "server.py").write_text("return 1\n", encoding="utf-8")
    compile_result = subprocess.run([sys.executable, "-m", "py_compile", "server.py"],
                                    cwd=tmp_path, capture_output=True, text=True, timeout=10, check=False)
    code = "import ast; ast.parse(open('server.py').read())"
    parse_result = subprocess.run([sys.executable, "-c", code],
                                  cwd=tmp_path, capture_output=True, text=True, timeout=10, check=False)
    assert compile_result.returncode != 0 and parse_result.returncode == 0
    state = TrajectoryState({}, goal="g", message_count=1)
    _run(state, "python -m py_compile server.py", compile_result.stderr, verdict="failed")
    _run(state, 'python -c "' + code + '"', parse_result.stdout)
    assert state.check_failing()
