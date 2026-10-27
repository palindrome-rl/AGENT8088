"""Asking whether something is installed, and a check the machine would not let run, are not failed checks.

Seen in real runs, where every "the latest check failed ... treat the result as unfinished" over a finished app was one of these:
a `require.resolve('playwright') ... && where msedge & where chrome` probe, a Playwright test that could not start a browser
(WinError 5 / Access is denied), an e2e script that printed NO CHROMIUM, and a `node smoke.js` that died with spawn EPERM.
A real test failure in the same output still counts."""
import pytest

from agent8088.trajectory import TrajectoryState, _environment_limit, _is_probe

WS = "C:\\w\\proj"
NODE_PROBE = ("cd /d \"E:\\w\\app\" && node -e \"try{require.resolve('playwright');console.log('playwright: yes')}catch(e){console.log('playwright: no')};"
              "try{require.resolve('puppeteer');console.log('puppeteer: yes')}catch(e){console.log('puppeteer: no')}\" "
              "&& where msedge 2>nul & where chrome 2>nul & where chromium 2>nul")
PY_PROBE = ("cd \"E:\\w\\app\" && python -c \"import playwright; print('playwright', playwright.__version__)\" 2>&1 "
            "&& python -c \"from playwright.sync_api import sync_playwright; print('sync_api OK')\" 2>&1")


@pytest.mark.parametrize("command", [
    NODE_PROBE, PY_PROBE, "where chrome", "which node", "pip show playwright", "python -m pip list", "npm ls playwright",
    "python -c \"import json, sys\"", "node -e \"console.log(require.resolve('playwright'))\"",
])
def test_availability_commands_are_probes(command):
    assert _is_probe(command)


@pytest.mark.parametrize("command", [
    "pytest", "node battle_test.js", "python -c \"import app; app.main()\"", "python -c \"import json; print(json.dumps(x))\"",
    "node -e \"require('./game.js').run()\"", "node -e \"console.log(1)\"", "where chrome && node test.js",
    "python -c \"import os; os.remove('a')\"", "npm test", "npm run build",
])
def test_anything_that_does_more_is_not_a_probe(command):
    assert not _is_probe(command)


def _state(written=("e2e.js", "smoke.js", "test_browser.py")):
    state = TrajectoryState({}, goal="g", message_count=1)
    state.data["written_paths"] = [f"{WS}\\{name}" for name in written]
    return state


def _run(state, command, result, verdict="failed"):
    operation = state.before_tool("execute_shell")
    state.after_tool(operation, result, failed=False, blocked=False, mutated=False, verdict=verdict, command=command)


def test_a_failed_probe_is_not_a_failing_check():
    state = _state()
    _run(state, NODE_PROBE, "playwright: no\npuppeteer: no\nCommand exited with status 1.")
    assert not state.check_failing()


SPAWN = "node:internal/child_process:420\n    throw new ErrnoException(err, 'spawn');\nError: spawn EPERM\n    at ChildProcess.spawn\nCommand exited with status 1."
LAUNCH = ("Traceback (most recent call last):\n  File \"playwright/_impl/_transport.py\", line 120, in connect\n"
          "PermissionError: [WinError 5] Access is denied\nCommand exited with status 1.")


@pytest.mark.parametrize("command, output, expected", [
    ("node smoke.js", SPAWN, "process"),
    ("python test_browser.py", LAUNCH, "browser"),
    ("node e2e.js", "server up on 8765\nNO CHROMIUM\nCommand exited with status 1.", "browser"),
    ("node e2e.js", "[0517/101200:ERROR:mojo\\core] Access is denied (0x5)", "browser"),
    ("node smoke.js", "browserType.launch: Executable doesn't exist at C:\\x\\chrome.exe", "browser"),
])
def test_an_environment_limit_is_a_note_not_a_failing_check(command, output, expected):
    state = _state()
    _run(state, command, output)
    assert not state.check_failing()
    assert expected in state.environment_note()


def test_a_real_failure_in_the_same_output_still_fails():
    state = _state()
    _run(state, "node smoke.js", "1 failed, 4 passed\n" + SPAWN)
    assert state.check_failing() and not state.environment_note()


def test_an_ordinary_crash_still_fails():
    state = _state()
    _run(state, "node smoke.js", "ReferenceError: board is not defined\nCommand exited with status 1.")
    assert state.check_failing()


def test_access_denied_without_a_browser_is_not_an_environment_limit():
    assert _environment_limit("PermissionError: [WinError 5] Access is denied: 'cache.json'") == ""


def test_the_same_limit_is_recorded_once():
    state = _state()
    for _ in range(3):
        _run(state, "node smoke.js", SPAWN)
    assert len(state.data["env_limits"]) == 1


def test_the_final_answer_says_it_was_not_verified(engine):
    state = _state()
    _run(state, "node smoke.js", SPAWN)
    answer = engine._with_task_notes("Built it.", state)
    assert "Not verified: the sandbox did not allow a process to be started" in answer
    assert "NOT VERIFIED: the latest check failed" not in answer
    assert "Incomplete: the latest automated check failed" not in answer
