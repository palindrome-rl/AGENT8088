import pytest
from agent8088.repository_access import _sparse_patterns, _checkout_budget


def test_default_only_root_files():
    assert _sparse_patterns('') == '/*\n!/*/\n'


def test_targeted_subtree():
    assert _sparse_patterns('docs/**') == '/docs/**\n'


@pytest.mark.parametrize('value',['../secret','a\nb','/absolute','!exclude','a\\b'])
def test_unsafe_selection(value):
    with pytest.raises(ValueError): _sparse_patterns(value)


def test_disk_limit_still_enforced(tmp_path):
    with (tmp_path/'large').open('wb') as f:
        f.truncate(129*1024*1024)
    with pytest.raises(ValueError, match='narrow include'):
        _checkout_budget(tmp_path)


def test_pack_rename_during_budget_scan(tmp_path, monkeypatch):
    from pathlib import Path
    target = tmp_path/'tmp_pack'
    target.write_bytes(b'pack')
    original = Path.lstat
    def renamed(path):
        if path == target: raise FileNotFoundError('pack was renamed')
        return original(path)
    monkeypatch.setattr(Path, 'lstat', renamed)
    _checkout_budget(tmp_path)
