"""A check_app run the model made itself counts: a failed page is a failing check until the same page passes.

Seen in a local-model run: the model's own check_app said "smoke test FAILED - Invalid or unexpected token", it stopped
at the repeated-call breaker, and the answer carried no headline because nothing had recorded that result as a failure."""
import pytest

from agent8088.trajectory import TrajectoryState

FAILED = 'index.html: smoke test FAILED - page error: Invalid or unexpected token; clicking the first control (button#go "Go") changed nothing'
PASSED = "index.html: smoke test passed (2 warnings)"


@pytest.fixture
def state():
    state = TrajectoryState({}, goal="g", message_count=1)
    state.data["mutation_count"] = 1
    return state


def _check_app(engine, state, result, status=None, path=""):
    operation = state.before_tool("check_app")
    status = engine._check_app_status("check_app", result, "unknown") if status is None else status
    state.after_tool(operation, result, failed=False, blocked=False, mutated=False, verdict=status, command="", path=path)
    state.settle_check_app(result, path=path)


def test_the_status_of_a_failed_check_app_is_failed(engine):
    assert engine._check_app_status("check_app", FAILED, "unknown") == "failed"


@pytest.mark.parametrize("result", [PASSED, "index.html: smoke test unavailable (no browser)", "Error: x does not exist",
                                    "app.py: smoke test skipped"])
def test_anything_else_keeps_its_status(engine, result):
    assert engine._check_app_status("check_app", result, "unknown") == "unknown"


def test_other_tools_are_not_read_this_way(engine):
    assert engine._check_app_status("read_text", FAILED, "unknown") == "unknown"
    assert engine._check_app_status("check_app", FAILED, "passed") == "passed"


def test_a_failed_page_check_is_a_failing_check(engine, state):
    _check_app(engine, state, FAILED)
    assert state.check_failing() and state.failing_check_detail().startswith("check_app -> index.html: smoke test FAILED")
    assert engine._with_task_notes("Done.", state).startswith("NOT VERIFIED:")


def test_a_later_pass_of_the_same_page_settles_it_but_is_not_a_test(engine, state):
    _check_app(engine, state, FAILED)
    _check_app(engine, state, PASSED)
    assert not state.check_failing()
    assert state.data.get("verification") != "verified"


def test_a_pass_of_another_page_does_not_settle_it(engine, state):
    _check_app(engine, state, FAILED)
    _check_app(engine, state, "other.html: smoke test passed")
    assert state.check_failing()


def test_a_pass_with_warnings_only_does_not_set_anything(engine, state):
    _check_app(engine, state, PASSED)
    assert not state.check_failing()


def test_an_unavailable_check_sets_nothing(engine, state):
    _check_app(engine, state, "index.html: smoke test unavailable (no browser)")
    assert not state.check_failing()


@pytest.mark.parametrize("automatic", [False, True])
def test_a_long_target_can_settle_its_own_failure(engine, state, automatic):
    path = 'site/' + 'nested/' * 28 + 'index.html'
    _check_app(engine, state, path + ': smoke test FAILED - broken')
    assert state.check_failing()
    _check_app(engine, state, 'other/index.html: smoke test passed')
    assert state.check_failing()
    if automatic:
        state.record_smoke(path, path + ': smoke test passed', False, passed=True)
    else:
        _check_app(engine, state, path + ': smoke test passed')
    assert not state.check_failing()


def test_two_pages_with_the_same_basename_are_not_the_same_check(engine, state):
    # Real Report.summary() only displays the basename. Identity must come
    # from the tool's actual path argument, not from those display words.
    _check_app(engine, state, FAILED, path='site-a/index.html')
    _check_app(engine, state, PASSED, path='site-b/index.html')
    assert state.check_failing()
    _check_app(engine, state, PASSED, path='site-a/index.html')
    assert not state.check_failing()


@pytest.mark.parametrize('second,same', [('site-b/index.html', False), ('site-a/index.html', True)])
def test_agent_loop_passes_actual_check_targets_to_the_tracker(engine, monkeypatch, tmp_path, second, same):
    import json
    from tests.test_completion_pipeline import Script, _call
    monkeypatch.setattr(engine, 'PROJECT_ROOT', tmp_path)
    monkeypatch.setattr(engine, 'ARTIFACTS_ROOT', tmp_path / 'artifacts')
    monkeypatch.setattr(engine, 'ALLOWED_PATHS', [tmp_path])
    monkeypatch.setattr(engine, 'PERMISSION_MODE', 'full-auto')
    results = iter([FAILED, PASSED])
    seen = []
    def tool(name, arguments, depth=0):
        assert name == 'check_app' and json.loads(arguments)['path']
        return next(results)
    Script(engine, monkeypatch, [
        _call('check_app', path='site-a/index.html'),
        _call('check_app', path=second), 'Finished the checks.',
    ], tool=tool)
    engine.run_agent([{'role': 'user', 'content': 'Check the pages and report the results.'}],
                     max_turns=8, tools_def=[], allowed_tools={'check_app'},
                     memory_capture=False, on_trajectory_state=seen.append)
    assert seen[-1]['check_failing'] is not same
