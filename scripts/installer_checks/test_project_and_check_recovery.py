"""Portable offline checks for project placement and verification recovery."""
import importlib

import pytest


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    monkeypatch.setenv('AGENT8088_HOME', str(tmp_path / 'home'))
    monkeypatch.setenv('AGENT8088_CONFIG', str(tmp_path / 'absent.txt'))
    from agent8088 import engine
    engine = importlib.reload(engine)
    monkeypatch.setattr(engine, 'PROJECT_ROOT', tmp_path)
    monkeypatch.setattr(engine, 'ARTIFACTS_ROOT', tmp_path / 'artifacts')
    monkeypatch.setattr(engine, 'ALLOWED_PATHS', [tmp_path])
    monkeypatch.setattr(engine, 'DISPOSABLE_CONTAINER', False)
    monkeypatch.setattr(engine, '_PROJECT_FOLDER_ROOT', {})
    monkeypatch.setattr(engine, '_CURRENT_RUN_MESSAGES', [
        {'role': 'user', 'content': 'Create demo/package.json.'}])
    return engine


def _write(runtime, name):
    path = runtime.resolve_write_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('x', encoding='utf-8')
    return path


def test_project_stays_together_after_its_folder_is_deleted(runtime, tmp_path):
    first = _write(runtime, 'demo/package.json')
    assert first == tmp_path / 'demo/package.json'
    assert _write(runtime, 'demo/style.css').parent == first.parent
    first.unlink()
    (first.parent / 'style.css').unlink()
    first.parent.rmdir()
    assert _write(runtime, 'demo/server.js').parent == tmp_path / 'artifacts/demo'
    assert _write(runtime, 'demo/package.json').parent == tmp_path / 'artifacts/demo'


def _run(state, command, text, verdict='unknown'):
    state.after_tool(state.before_tool('execute_shell'), text, failed=False,
                     blocked=False, mutated=False, verdict=verdict, command=command)


def test_a_failed_suite_cannot_be_cleared_by_plain_script_startup():
    from agent8088.trajectory import TrajectoryState
    state = TrajectoryState({}, goal='verify', message_count=1)
    state.data['written_paths'] = ['watch.py']
    _run(state, 'python -m pytest watch.py', 'FAILED (failures=1)', 'failed')
    _run(state, 'python watch.py', 'started')
    assert state.check_failing()


def test_clean_script_retry_settles_but_a_traceback_does_not():
    from agent8088.trajectory import TrajectoryState
    state = TrajectoryState({}, goal='verify', message_count=1)
    state.data['written_paths'] = ['watch.py']
    _run(state, 'python watch.py', 'ValueError: bad', 'failed')
    _run(state, 'python watch.py', 'ValueError: still bad')
    assert state.check_failing()
    _run(state, 'python watch.py 2>&1', 'ready')
    assert not state.check_failing()


def test_page_recovery_uses_the_actual_target_not_a_short_display_name(runtime):
    from agent8088.trajectory import TrajectoryState
    state = TrajectoryState({}, goal='verify', message_count=1)
    path = 'site/' + 'nested/' * 28 + 'index.html'
    result = 'index.html: smoke test FAILED - broken'
    state.after_tool(state.before_tool('check_app'), result, failed=False,
                     blocked=False, mutated=False, path=path,
                     verdict=runtime._check_app_status('check_app', result, 'unknown'))
    state.settle_check_app('index.html: smoke test passed', path='other/index.html')
    assert state.check_failing()
    state.settle_check_app('index.html: smoke test passed', path=path)
    assert not state.check_failing()


@pytest.mark.parametrize('reference', ['client/main.js', 'https://example.com/main.js', '//example.com/main.js'])
def test_matching_basename_does_not_make_a_script_browser_loaded(tmp_path, reference):
    from agent8088 import app_check
    (tmp_path / 'index.html').write_text(f'<script src="{reference}"></script>', encoding='utf-8')
    script = tmp_path / 'tools/main.js'
    script.parent.mkdir()
    script.write_text('missingFunction();', encoding='utf-8')
    assert app_check.loaded_by_page(script) is None


@pytest.mark.parametrize('conjunction', ['and', 'then', 'but'])
def test_required_output_in_a_new_clause_is_not_an_input(conjunction):
    from agent8088.completion import is_input_reference
    assert not is_input_reference(f'See notes.md {conjunction} write spec.json.', 'spec.json')


@pytest.mark.parametrize('name', ['selfSpAtkUp2', 'selfAtkUp1SpAtkUp1SpdUp1DefDown1SpDefDown1',
                                  'test_delete_returns_404_for_missing_note'])
def test_structured_code_identifiers_are_not_credentials(name):
    from agent8088.secret_scan import find_secrets
    assert not find_secrets(name)


def test_known_vendor_prefix_is_still_detected():
    from agent8088.secret_scan import find_secrets
    synthetic = 'ghp_' + ('0123456789' * 4)
    assert find_secrets(synthetic)


def test_tested_notes_state_their_scope(runtime):
    from agent8088.trajectory import TrajectoryState
    state = TrajectoryState({}, goal='verify', message_count=1)
    state.data.update(mutation_count=1, verification='verified')
    note = runtime._with_task_notes('Done.', state)
    assert "the run's own tests" in note
    assert 'does not check the result against what was asked' in note
