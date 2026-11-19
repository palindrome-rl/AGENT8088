import asyncio
from types import SimpleNamespace
from starlette.requests import Request
from agent8088 import web_server as web
from agent8088 import document_access


def request(name, body):
    async def receive():
        return {'type':'http.request', 'body':body, 'more_body':False}
    return Request({'type':'http', 'headers':[(b'x-filename',name.encode())]}, receive)


def setup(monkeypatch, tmp_path):
    a = SimpleNamespace(ARTIFACTS_ROOT=tmp_path)
    c = SimpleNamespace(S=SimpleNamespace(name='test-session'))
    monkeypatch.setattr(web, '_eng', lambda:a)
    monkeypatch.setattr(web, '_cl', lambda:c)
    return a,c


def test_concurrent_uploads_keep_both_entries(monkeypatch,tmp_path):
    a,c = setup(monkeypatch,tmp_path)
    async def run():
        return await asyncio.gather(web.upload_attachment(request('a.txt',b'alpha')), web.upload_attachment(request('b.txt',b'beta')))
    results = asyncio.run(run())
    assert len(web._read_attachments(a,c)) == 2
    assert len(web._validated_attachments([r['id'] for r in results],a,c)) == 2
    assert all(r['status']=='ready' for r in results)


def test_corrupt_pdf_reports_attention_not_ready(monkeypatch,tmp_path):
    setup(monkeypatch,tmp_path)
    result = asyncio.run(web.upload_attachment(request('broken.pdf',b'not a pdf')))
    assert result['status']=='needs_attention'
    assert result['warning']


def test_foreign_session_id_is_rejected(monkeypatch,tmp_path):
    a,c=setup(monkeypatch,tmp_path)
    result=asyncio.run(web.upload_attachment(request('a.txt',b'alpha')))
    c.S.name='other'
    import pytest
    with pytest.raises(ValueError,match='does not belong'):
        web._validated_attachments([result['id']],a,c)


def test_delete_clears_derivatives(monkeypatch,tmp_path):
    a,c=setup(monkeypatch,tmp_path)
    result=asyncio.run(web.upload_attachment(request('a.txt',b'alpha')))
    path=web._validated_attachments([result['id']],a,c)[0]['path']
    document_access.process(path,'task',lambda *a:'evidence')
    asyncio.run(web.delete_attachment(result['id']))
    assert not list(tmp_path.rglob('*.txt'))
    assert not list(tmp_path.rglob('*-*.json'))


def test_history_keeps_attachment_chip_but_hides_model_context():
    attachment_id = 'a' * 32
    message = {
        'role': 'user',
        'content': ('Please summarize this.' + web._ATTACHMENT_CONTEXT_HEADER
                    + f'- report.pdf (attachment {attachment_id}): uploads/report.pdf'),
    }
    public = web._history_message(message)
    assert public['content'] == 'Please summarize this.'
    assert public['attachments'] == [{'id': attachment_id, 'name': 'report.pdf'}]
