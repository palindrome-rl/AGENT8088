import subprocess
import sys
import importlib.util
import pytest


@pytest.mark.skipif(importlib.util.find_spec('gitingest') is None, reason='optional repository extra')
def test_repository_import_preserves_logging_in_fresh_process():
    code = '''
import logging, sys
from agent8088.repository_access import _ignore_patterns
root = logging.getLogger()
app = logging.getLogger('agent8088')
handler = logging.NullHandler()
app.addHandler(handler)
app.propagate = False
before = (list(root.handlers), root.level, list(app.handlers), app.propagate)
assert _ignore_patterns()
assert 'gitingest' not in sys.modules
assert before == (list(root.handlers), root.level, list(app.handlers), app.propagate)
print('clean')
'''
    result = subprocess.run([sys.executable,'-c',code],capture_output=True,text=True,timeout=20)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'clean'
    assert result.stderr == ''


def test_memory_capture_never_joins(monkeypatch):
    from agent8088 import cli
    class Busy:
        def is_alive(self): return True
        def join(self, *args, **kwargs): raise AssertionError('must not wait')
    worker = Busy()
    monkeypatch.setattr(cli.S, 'memory_notifications', 'on')
    monkeypatch.setattr(cli.A, 'memory_capture_thread', worker)
    monkeypatch.setattr(cli, '_pending_captures', [])
    rows = []
    cli._await_memory_capture(rows)
    assert cli._pending_captures == [(worker, rows)]
