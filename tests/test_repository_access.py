import os
from pathlib import Path
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


@pytest.mark.parametrize('pattern', ['notes.md', '**/notes.md', 'vault/notes.md'])
def test_an_include_naming_a_protected_file_cannot_restore_it(tmp_path, pattern):
    """The filter selects among permitted files; it never widens permission.

    GitIngest's own include patterns take precedence over its excludes, so the
    ordering here is load-bearing: `permitted()` runs during the walk, before
    the include glob is consulted.

    Deliberately not `.env`: that name is in GitIngest's DEFAULT_IGNORE_PATTERNS
    and would be withheld even if `permitted()` were bypassed entirely, so a
    test using it passes whatever the ordering is. `notes.md` is an ordinary
    name no default rule touches, which makes `permitted()` the only thing
    standing between the include pattern and the content.
    """
    (tmp_path/'vault').mkdir()
    (tmp_path/'notes.md').write_text('SECRET=hidden', encoding='utf-8')
    (tmp_path/'vault'/'notes.md').write_text('SECRET=hidden', encoding='utf-8')
    (tmp_path/'main.py').write_text('one', encoding='utf-8')

    result = json.loads(r.inspect(tmp_path, {'include': pattern},
                                  permitted=lambda p: p.name != 'notes.md'))

    assert result['paths'] == []
    assert 'SECRET=hidden' not in json.dumps(result)


def test_the_resolved_commit_is_recorded_in_a_git_worktree(tmp_path):
    """`.git` is a FILE in a worktree (and a submodule), not a directory.

    The design note required recording the resolved commit rather than a
    movable branch name. Gating that on `.git` being a directory silently
    dropped the revision for any worktree or submodule source -- the result
    simply had no `git_commit` key and nothing said why.
    """
    import subprocess
    env = {**os.environ, 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull,
           'GIT_AUTHOR_NAME': 'T', 'GIT_AUTHOR_EMAIL': 't@e',
           'GIT_COMMITTER_NAME': 'T', 'GIT_COMMITTER_EMAIL': 't@e'}
    main = tmp_path/'main'
    main.mkdir()
    (main/'main.py').write_text('one', encoding='utf-8')
    for argv in (['init', '-b', 'trunk'], ['add', '-A'], ['commit', '-m', 'first']):
        subprocess.run(['git', '-C', str(main), *argv], check=True,
                       capture_output=True, env=env)
    head = subprocess.run(['git', '-C', str(main), 'rev-parse', 'HEAD'],
                          capture_output=True, text=True, env=env).stdout.strip()
    linked = tmp_path/'linked'
    subprocess.run(['git', '-C', str(main), 'worktree', 'add', '--detach', str(linked)],
                   check=True, capture_output=True, env=env)
    assert (linked/'.git').is_file(), 'precondition: a worktree .git is a file'

    result = access(linked)

    assert result['git_commit'] == head


def test_overview_carries_the_directory_tree(tmp_path):
    """The design note asks overview for a bounded *tree*, not just a path list.

    GitIngest already computes one on the same call whose summary we keep, so
    dropping it threw away the one output that shows hierarchy -- the thing a
    flat list of relative paths makes the model reconstruct for itself.
    """
    (tmp_path/'core').mkdir()
    (tmp_path/'core'/'deep').mkdir()
    (tmp_path/'top.py').write_text('one', encoding='utf-8')
    (tmp_path/'core'/'mid.py').write_text('two', encoding='utf-8')
    (tmp_path/'core'/'deep'/'leaf.py').write_text('three', encoding='utf-8')

    tree = access(tmp_path)['digest']['tree']

    assert 'core/' in tree and 'leaf.py' in tree
    assert str(tmp_path) not in tree, 'the staging path must not leak into the tree'


def test_the_tree_is_bounded(tmp_path):
    """A wide repository must not turn the manifest into the payload."""
    for n in range(400):
        (tmp_path/f'file{n:04d}.py').write_text('x', encoding='utf-8')

    tree = access(tmp_path)['digest']['tree']

    assert len(tree) <= 4200, f'unbounded tree: {len(tree)} chars'


def test_an_explicit_include_reaches_into_a_default_ignored_directory(tmp_path):
    """Convenience defaults must yield to the caller; protection must not.

    GitIngest ships 133 default ignore patterns, and `pkg/` is one of them --
    which is exactly where a Go project keeps its library source. Applying
    those defaults ahead of `include` made that source unreachable: neither
    `include='pkg/**'` nor `include='**/*.go'` could surface it, and the whole
    subtree collapsed into a single `{'ignored': 1}` count. The design note
    calls these "convenience filters, not a security boundary", so an include
    pattern that names a location explicitly is an instruction to look there.

    The rule is deliberately narrow: only a pattern with a literal directory
    prefix counts as naming a location. `**/*.go` does not ask for `pkg/`, and
    must not drag `node_modules` back in either.
    """
    (tmp_path/'pkg').mkdir()
    (tmp_path/'cmd').mkdir()
    (tmp_path/'node_modules').mkdir()
    (tmp_path/'pkg'/'server.go').write_text('package server', encoding='utf-8')
    (tmp_path/'cmd'/'main.go').write_text('package main', encoding='utf-8')
    (tmp_path/'node_modules'/'junk.go').write_text('package junk', encoding='utf-8')

    assert access(tmp_path)['paths'] == ['cmd/main.go']
    assert access(tmp_path, include='pkg/**')['paths'] == ['pkg/server.go']
    assert access(tmp_path, include='**/*.go')['paths'] == ['cmd/main.go']


def test_an_include_still_cannot_reach_a_protected_path(tmp_path):
    """The override applies to convenience defaults only, never to permitted()."""
    (tmp_path/'pkg').mkdir()
    (tmp_path/'pkg'/'notes.md').write_text('SECRET=hidden', encoding='utf-8')

    result = json.loads(r.inspect(tmp_path, {'include': 'pkg/**'},
                                  permitted=lambda p: p.name != 'notes.md'))

    assert result['paths'] == []
    assert 'SECRET=hidden' not in json.dumps(result)


@pytest.mark.parametrize('layout', [
    {'include': 'pkg/**', 'files': {'pkg/server.go': 'package server'}},
    {'include': '', 'files': {'a.py': 'one', 'sub/b.py': 'two'}},
])
def test_the_digest_agrees_with_the_manifest(tmp_path, layout):
    """The adapter must be verified against the manifest, not trusted beside it.

    The staging directory holds only files this module already approved, but
    GitIngest re-applied its own 133 default ignores to it, so anything living
    under a default-ignored name was counted twice differently: the manifest
    said one file, the summary said "Files ingested: 0", and the tree came back
    empty. Two numbers for the same snapshot is worse than either alone.
    """
    for name, body in layout['files'].items():
        target = tmp_path/name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding='utf-8')

    result = json.loads(r.inspect(tmp_path, {'include': layout['include']},
                                  permitted=lambda p: True))

    assert f"Files ingested: {result['files']}" in result['digest']['summary']
    for name in result['paths']:
        assert Path(name).name in result['digest']['tree']


def test_the_include_override_reaches_nested_directories(tmp_path):
    """A gitignore rule on `pkg/` also matches `pkg/api/`, so exempting only
    the named directory returned a silently partial subtree: pkg/server.go but
    not pkg/api/v1.go, with nothing to say a level had been dropped."""
    (tmp_path/'pkg'/'api').mkdir(parents=True)
    (tmp_path/'pkg'/'server.go').write_text('package server', encoding='utf-8')
    (tmp_path/'pkg'/'api'/'v1.go').write_text('package v1', encoding='utf-8')

    assert access(tmp_path, include='pkg/**')['paths'] == ['pkg/api/v1.go', 'pkg/server.go']
