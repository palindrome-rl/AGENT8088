"""Public-release regressions for safe polling and honest app verification."""
import time

import pytest

from agent8088 import app_check
from agent8088.trajectory import TrajectoryState


def test_shipped_shell_default_and_replay_limits(engine):
    assert engine.TOOL_SPECS['execute_shell']['timeout'] == 60
    old = time.monotonic() - 3600
    assert engine._poll_refresh_due('execute_shell', 'ok', old, 0, 'dir')
    assert not engine._poll_refresh_due('execute_shell', 'ok', old, engine.REPEAT_REFRESH_MAX, 'dir')
    assert not engine._poll_refresh_due('execute_shell', 'ok', time.monotonic(), 0, 'dir')
    assert not engine._poll_refresh_due('execute_shell', 'output\n' * 150 + 'Command exited with status 1', old, 0, 'dir')


@pytest.mark.parametrize('command', [
    'echo charged >> ledger.txt', 'curl -X POST http://localhost/api',
    'sleep 20; touch ledger.txt', 'curl --config request.conf http://localhost/api',
    'powershell -c Start-Sleep 20; Remove-Item ledger.txt',
    'python -c "import os,time; os.remove(\'ledger.txt\'); time.sleep(20)"',
])
def test_side_effects_cannot_be_automatically_replayed(engine, command):
    assert not engine._safe_poll_command(command)
    assert not engine._is_wait_command('execute_shell', {'command': command})


def test_supported_read_only_polls_are_retained(engine):
    assert engine._safe_poll_command('python -c "import os; print(os.path.getsize(\'job.log\'))"')
    assert engine._is_wait_command('execute_shell', {'command': 'cd /app && sleep 5 && curl -s localhost:5000'})


def test_authored_docs_are_checked_but_known_downloaded_references_can_be_skipped(tmp_path):
    paths = [tmp_path / 'index.html', tmp_path / 'docs/guide.html', tmp_path / 'data/reference.html']
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('<html></html>', encoding='utf-8')
    targets = app_check.deliverable_targets([str(p) for p in paths], [tmp_path], reference_pages=[str(paths[2])])
    assert {p.name for p in targets} == {'index.html', 'guide.html'}


def test_a_temp_workspace_does_not_hide_a_real_app():
    state = TrajectoryState({}, goal='Create a web app', message_count=1, workspace='C:/Temp/project')
    state.data.update(mutation_count=1, ran_ok_at=1, written_paths=['C:/Temp/project/docs/index.html'])
    assert state.has_app_entry()
    assert state.assurance() == 'exercised'
    state.data['written_paths'] = ['fetch.py']
    assert state.no_runnable_app()
    assert state.assurance() == 'inspected'


def test_manual_check_retains_even_a_clipped_backend_warning(engine):
    warning = app_check._blocked_request_warning('http://[::1]:8000/api', {'POST'})
    assert 'http://[::1]:8000/api' in warning and 'NOT verified' in warning
    report = app_check.Report('web', 'index.html', app_check.PASS, warnings=['other warning'] * 6 + [warning])
    result = report.text()
    assert warning not in result
    state = TrajectoryState({}, goal='Create a web app', message_count=1)
    state.after_tool(state.before_tool('check_app'), result, failed=False, blocked=False,
                     mutated=False, path='index.html')
    assert 'Not verified: the page calls its own server or a local service' in engine._with_task_notes('Done.', state)


def test_browser_timeout_never_proves_a_machine_fault():
    report = app_check.Report('web', 'index.html', app_check.UNAVAILABLE, note='the browser check did not finish in 15s')
    assert not app_check.environment_unavailable(report)
    assert app_check.browser_unavailable(report)
    assert 'not a defect in the page' not in app_check.UNAVAILABLE_GUIDANCE
