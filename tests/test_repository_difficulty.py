import json
import pytest
import os
import time
from agent8088 import repository_access as r


def read(root, **args):
    return json.loads(r.inspect(root,args,permitted=lambda p: p.name != '.env'))


def test_real_aggregate_budget(tmp_path):
    for index in range(33):
        (tmp_path/f'{index}.txt').write_bytes(b'a'*r.MAX_FILE)
    with pytest.raises(ValueError, match='8 MiB'):
        read(tmp_path)


def test_large_file_is_reported_not_read(tmp_path):
    (tmp_path/'large.txt').write_bytes(b'a'*(r.MAX_FILE+1))
    assert read(tmp_path)['skipped']['oversized_or_special'] == 1


def test_search_limit_is_explicit(tmp_path):
    (tmp_path/'many.txt').write_text('needle\n'*100,encoding='utf-8')
    result=read(tmp_path,action='search',query='needle')
    assert len(result['matches'])==12
    assert result['partial_evidence'] is True


def test_scope_change_rejects_old_snapshot(tmp_path):
    (tmp_path/'one.txt').write_text('one')
    (tmp_path/'two.txt').write_text('two')
    version=read(tmp_path)['snapshot_id']
    with pytest.raises(ValueError, match='snapshot changed'):
        read(tmp_path,include='one.txt',snapshot_id=version)


def test_unicode_pagination_exact(tmp_path):
    value='αβγ\n'*4000
    (tmp_path/'text.txt').write_bytes(value.encode())
    cursor=0
    pieces=[]
    while cursor is not None:
        result=read(tmp_path,action='read',path='text.txt',cursor=cursor)
        pieces.append(result['text'])
        cursor=result['next_cursor']
    assert ''.join(pieces)==value


@pytest.mark.skipif(not os.environ.get('LIVE_REPOSITORY_TESTS'), reason='opt-in network check')
def test_live_clone_cancellation():
    started=time.monotonic()
    def stop():
        if time.monotonic()-started > .05:
            raise RuntimeError('QA cancellation')
    with pytest.raises(RuntimeError, match='QA cancellation'):
        with r.checkout('https://github.com/octocat/Hello-World',check=stop):
            pytest.fail('Clone completed before interruption')
    assert time.monotonic()-started < 10
