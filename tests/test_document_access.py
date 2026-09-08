import json
import pytest
from agent8088 import document_access as access


def test_cursor_roundtrip_long_lines_and_unicode(tmp_path):
    source = ('long line ' + '\u03b1' * 30000 + '\n') * 4
    path = tmp_path / 'source.txt'
    path.write_text(source, encoding='utf-8')
    cursor, pieces = 0, []
    while cursor is not None:
        result = json.loads(access.read(path, {'action': 'read', 'cursor': cursor}))
        pieces.append(result['text'])
        cursor = result['next_cursor']
    assert ''.join(pieces) == source


def test_focused_search_is_explicitly_partial(tmp_path):
    path = tmp_path / 'source.txt'
    path.write_text('alpha\n' * 20000 + 'important finding', encoding='utf-8')
    result = json.loads(access.read(path, {'action': 'search', 'query': 'important'}))
    assert result['partial_evidence']
    assert result['match_count'] == 1
    assert 'important finding' in result['matches'][0]['text']


def test_changed_document_invalidates_version(tmp_path):
    path = tmp_path / 'source.txt'
    path.write_text('original', encoding='utf-8')
    version = json.loads(access.read(path, {}))['version']
    path.write_text('replacement content', encoding='utf-8')
    with pytest.raises(ValueError, match='changed'):
        access.read(path, {'action': 'read', 'version': version})


def test_repeated_reads_extract_once(tmp_path, monkeypatch):
    path = tmp_path / 'source.pdf'
    path.write_bytes(b'fixture')
    calls = []
    monkeypatch.setattr(access, '_extract_text_bounded', lambda p: calls.append(p) or 'text')
    access.read(path, {})
    access.read(path, {'action': 'read'})
    assert len(calls) == 1


def test_model_receives_complete_document_chunk(engine):
    payload = 'x' * 25000
    assert engine._tool_result_for_model('document_read', payload) == payload


def test_process_covers_all_ranges_and_resumes(tmp_path):
    path = tmp_path / 'source.txt'
    path.write_text('a' * 3500, encoding='utf-8')
    # Fail a fixed chunk, not the Nth call: with requests in flight
    # concurrently, call order is not the source order.
    def first(task, passage):
        if 'Characters 2000-3000' in passage:
            raise RuntimeError('transient')
        return 'evidence'
    with pytest.raises(RuntimeError):
        access.process(path, 'list claims', first, chunk_chars=1000)
    resumed = []
    result = json.loads(access.process(path, 'list claims', lambda t,p: resumed.append(p) or 'remaining', chunk_chars=1000))
    assert len(resumed) == 2
    assert result['all_chunks_processed']
    assert [(n['start'], n['end']) for n in result['notes']] == [(0,1000),(1000,2000),(2000,3000),(3000,3500)]


def test_process_preflight_limit_makes_no_model_calls(tmp_path):
    path = tmp_path / 'source.txt'
    path.write_text('x' * 4000, encoding='utf-8')
    with pytest.raises(ValueError, match='limit'):
        access.process(path, 'task', lambda *a: pytest.fail('must not call model'), chunk_chars=1000, max_chunks=2)


def test_cancel_before_call(tmp_path):
    path = tmp_path / 'source.txt'
    path.write_text('text', encoding='utf-8')
    def cancel():
        raise RuntimeError('cancelled')
    with pytest.raises(RuntimeError, match='cancelled'):
        access.process(path, 'task', lambda *a: pytest.fail('must not call model'), check=cancel)


def test_checkpoint_survives_cache_clear_and_delete(tmp_path):
    path = tmp_path / '.web-attachments' / 'session' / 'document.txt'
    path.parent.mkdir(parents=True)
    path.write_text('text', encoding='utf-8')
    access.process(path, 'task', lambda *a: 'evidence')
    access._jobs.clear()
    result = json.loads(access.process(path, 'task', lambda *a: pytest.fail('checkpoint lost')))
    assert result['all_chunks_processed']
    access.discard(path)
    assert not list((path.parent / '.document-jobs').glob('*.json'))


def test_cross_session_read_denied(engine, monkeypatch, tmp_path):
    own = tmp_path / '.web-attachments' / 'own'
    other = tmp_path / '.web-attachments' / 'other' / 'secret.txt'
    other.parent.mkdir(parents=True)
    other.write_text('secret', encoding='utf-8')
    monkeypatch.setattr(engine, '_document_root', own)
    monkeypatch.setattr(engine, 'resolve_user_path', lambda value: other)
    result = engine.exec_tool('read_text', json.dumps({'filename':str(other)}))
    assert 'does not belong' in result


def test_workbook_preserves_formula_and_coordinates(tmp_path):
    from openpyxl import Workbook
    path = tmp_path / 'book.xlsx'
    book = Workbook()
    book.active['A1'] = 7
    book.active['A2'] = 3
    book.active['A3'] = '=SUM(A1:A2)'
    book.save(path)
    text = access.load(path)
    assert 'A3' in text and '=SUM(A1:A2)' in text
    assert 'not calculated results' in text


def test_scanned_pdf_requires_ocr(tmp_path):
    from pypdf import PdfWriter
    path = tmp_path / 'scan.pdf'
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    with path.open('wb') as stream:
        writer.write(stream)
    with pytest.raises(ValueError, match='OCR'):
        access.load(path)


def test_unsupported_binary_is_not_decoded_as_text(tmp_path):
    path = tmp_path / 'image.png'
    path.write_bytes(b'\x89PNG')
    with pytest.raises(ValueError, match='visual'):
        access.load(path)


def test_process_keeps_more_than_two_requests_in_flight(tmp_path):
    """A rolling window must run `concurrency` chunks at once, not pairs."""
    path = tmp_path / 'source.txt'
    path.write_text('a' * 8000, encoding='utf-8')
    import threading
    gate = threading.Barrier(4, timeout=15)
    def complete(task, passage):
        gate.wait()  # only reachable if four calls are in flight together
        return 'evidence'
    result = json.loads(access.process(path, 'task', complete, chunk_chars=1000, concurrency=4))
    assert result['all_chunks_processed']
    assert len(result['notes']) == 8


def test_process_commits_in_source_order_when_later_chunks_finish_first(tmp_path):
    import time
    path = tmp_path / 'source.txt'
    path.write_text('a' * 5000, encoding='utf-8')
    def complete(task, passage):
        start = int(passage.split('Characters ')[1].split('-')[0])
        time.sleep(0.30 if start == 0 else 0.02)  # chunk 0 finishes last
        return f'evidence-{start}'
    result = json.loads(access.process(path, 'task', complete, chunk_chars=1000, concurrency=5))
    assert [n['start'] for n in result['notes']] == [0, 1000, 2000, 3000, 4000]
    assert [n['evidence'] for n in result['notes']] == [f'evidence-{i*1000}' for i in range(5)]


def test_failed_chunk_does_not_spend_calls_on_the_whole_document(tmp_path):
    path = tmp_path / 'source.txt'
    path.write_text('a' * 40000, encoding='utf-8')  # 40 chunks
    calls = []
    lock = __import__('threading').Lock()
    def complete(task, passage):
        with lock:
            calls.append(passage)
        raise RuntimeError('provider down')
    with pytest.raises(RuntimeError, match='provider down'):
        access.process(path, 'task', complete, chunk_chars=1000, concurrency=4)
    assert len(calls) <= 8, f'first failure should stop the run, made {len(calls)} calls'


def test_chunk_budget_survives_completion_ceiling_at_or_above_context(engine):
    """Shipped defaults put max_completion_tokens above context_window."""
    chunk, _read, _out = engine._document_budgets(32768, 65000)
    chunks = (374711 + chunk - 1) // chunk
    assert chunk > 40000, f'chunk collapsed to {chunk}'
    assert chunks <= 48, f'87-page report needs {chunks} chunks, over the cap'


def test_document_budgets_fit_the_context_window(engine):
    for context in (8192, 32768, 128000, 200000):
        chunk, read_chars, output = engine._document_budgets(context, 65000)
        assert chunk >= 1000 and read_chars >= 1000 and output >= 512
        assert chunk // 3 + output < context, f'chunk+output overflows ctx={context}'
        assert read_chars // 3 + output < context
