"""Words inside inline interpreter code do not make a command a check.

Seen in a benchmark run: `npm install ... && node -e "console.log('node spawn test'); ..."` failed on a quoting
mistake in the snippet. The word "test" in the snippet made the whole command "the latest check", and the run ended
"the latest check failed ... treat the result as unfinished" although `npm run build` and the program both ran
cleanly afterwards."""
import pytest

from agent8088.trajectory import TrajectoryState, _is_check_run

SNIPPET = ("npm install --save-dev esbuild --ignore-scripts && dir node_modules\\@esbuild\\win32-x64\\esbuild.exe && "
           "node -e \"console.log('node spawn test'); const {spawn}=require('child_process'); spawn('node')\"")
SNIPPET_FAILED = ("added 2 packages, and audited 4 packages in 4s\n[eval]:1\nconsole.log('node spawn test');\n\n"
                  "SyntaxError: Invalid or unexpected token\n    at makeContextifyScript (node:internal/vm:185:14)\n"
                  "Node.js v22.11.0\nCommand exited with status 1.")


@pytest.mark.parametrize("command", [
    SNIPPET,
    "node -e \"console.log('test ok')\"",
    "python -c \"print('build finished')\"",
    "python3 -c 'import json; print(json.dumps({\"check\": 1}))'",
    "\"C:\\Program Files\\nodejs\\node.exe\" --no-warnings -e \"console.log('verify')\"",
    "deno eval 'console.log(1)'",
])
def test_inline_code_words_do_not_make_a_check(command):
    assert not _is_check_run(command)


@pytest.mark.parametrize("command", [
    "npm test",
    "npm run build",
    "python -m pytest tests",
    "pytest -q && node -e \"console.log('done')\"",
    "python -c \"import pytest; raise SystemExit(pytest.main(['-q']))\"",
    "node --test tests/",
])
def test_real_checks_are_still_checks(command):
    assert _is_check_run(command)


def _state():
    return TrajectoryState({}, goal="g", message_count=1)


def _shell(state, command, result, *, failed, verdict="unknown"):
    operation = state.before_tool("execute_shell")
    state.after_tool(operation, result, failed=failed, blocked=False, mutated=False, verdict=verdict,
                     command=command)


def test_a_failing_snippet_is_not_the_latest_check():
    state = _state()
    _shell(state, SNIPPET, SNIPPET_FAILED, failed=True, verdict="failed")
    assert not state.check_failing()


def test_a_failing_build_is_still_the_latest_check():
    state = _state()
    _shell(state, "npm run build", "Error: Could not resolve \"dayjs\"\nCommand exited with status 1.",
           failed=True, verdict="failed")
    assert state.check_failing()
