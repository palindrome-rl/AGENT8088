import json
import pytest
from agent8088 import repository_access as r

pytest.importorskip('gitingest')


def access(root, **args):
    return json.loads(r.inspect(root, args, permitted=lambda p: p.name != '.env'))


def test_overview_search_read(tmp_path):
    (tmp_path/'main.py').write_text('hello\nworld\n', encoding='utf-8')
    result = access(tmp_path)
    assert result['paths'] == ['main.py']
    assert result['digest']['summary']
    assert access(tmp_path, action='search', query='world')['matches'][0]['line'] == 2
    assert access(tmp_path, action='read', path='main.py')['text'] == (tmp_path/'main.py').read_bytes().decode()


def test_protection_and_stale_snapshot(tmp_path):
    (tmp_path/'.env').write_text('SECRET=hidden')
    (tmp_path/'main.py').write_text('one')
    result = access(tmp_path, action='read', path='main.py')
    with pytest.raises(ValueError, match='included file'):
        access(tmp_path, action='read', path='.env', include='*')
    (tmp_path/'main.py').write_text('two')
    with pytest.raises(ValueError, match='snapshot changed'):
        access(tmp_path, action='read', path='main.py', snapshot_id=result['snapshot_id'])


@pytest.mark.parametrize('url', ['http://github.com/a/b', 'https://user:token@github.com/a/b',
                               'https://127.0.0.1/a/b', 'https://github.com/a/b?token=x'])
def test_remote_validation(url):
    with pytest.raises(ValueError):
        r.remote_url(url)


def test_ignore_and_binary(tmp_path):
    (tmp_path/'.gitignore').write_text('hidden.txt\n')
    (tmp_path/'hidden.txt').write_text('hidden')
    (tmp_path/'binary').write_bytes(b'a\0b')
    result = access(tmp_path, action='search', query='hidden')
    assert all(m['path'] != 'hidden.txt' for m in result['matches'])
    assert result['skipped']['binary'] == 1


def test_read_continuation_and_redaction(tmp_path):
    (tmp_path/'source.txt').write_bytes(('α'*14000).encode())
    cursor, pieces = 0, []
    while cursor is not None:
        result = access(tmp_path, action='read', path='source.txt', cursor=cursor)
        pieces.append(result['text'])
        cursor = result['next_cursor']
    assert ''.join(pieces) == 'α'*14000


def test_cancel(tmp_path):
    def stop(): raise RuntimeError('cancelled')
    with pytest.raises(RuntimeError, match='cancelled'):
        r.inspect(tmp_path, {}, permitted=lambda p: True, check=stop)


def test_remote_readonly_requires_approval(monkeypatch):
    from agent8088 import engine
    monkeypatch.setattr(engine, 'PERMISSION_MODE', 'readonly')
    monkeypatch.setattr(engine, '_egress_check', lambda url: None)
    monkeypatch.setattr(engine, '_ssrf_check', lambda url: None)
    calls = []
    def gate(*args, **kwargs):
        calls.append(kwargs)
        return False
    monkeypatch.setattr(engine, 'check_permission', gate)
    result = engine.run_tool('repository_read', {'source':'https://github.com/octocat/Hello-World'})
    assert 'ESCALATION_REQUEST' in result
    assert calls[0]['host'] is True


def test_remote_plan_mode_never_clones(monkeypatch):
    from agent8088 import engine
    monkeypatch.setattr(engine, 'PERMISSION_MODE', 'plan-only')
    monkeypatch.setattr(engine, '_egress_check', lambda url: None)
    monkeypatch.setattr(engine, '_ssrf_check', lambda url: None)
    def forbidden(*args, **kwargs): raise AssertionError('must not clone')
    monkeypatch.setattr(r, 'checkout', forbidden)
    result = engine.run_tool('repository_read', {'source':'https://github.com/octocat/Hello-World'})
    assert 'plan' in result.lower()
