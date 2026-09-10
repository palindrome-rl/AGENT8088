import importlib.util
from pathlib import Path
from types import SimpleNamespace as NS

import httpx
import pytest

from agent8088 import llmrouter_adapter as adapter


def transport(monkeypatch, result=None, status=200):
    calls = []
    real = httpx.Client
    def handle(request):
        calls.append(request)
        return httpx.Response(status, json=result or {'model_name': 'p:strong'})
    monkeypatch.setattr(adapter.httpx, 'Client', lambda **kw: real(transport=httpx.MockTransport(handle), **kw))
    monkeypatch.setenv('AGENT8088_ROUTER_TOKEN', 'test-only')
    return calls


@pytest.mark.parametrize('mode,expected', [('off', None), ('shadow', None), ('enabled', ('p', 'strong'))])
def test_modes(monkeypatch, mode, expected):
    calls = transport(monkeypatch)
    selected, meta = adapter.recommend({'llmrouter_mode': mode}, [{'role': 'user', 'content': 'hello'}], [('p', 'strong')])
    assert selected == expected
    assert len(calls) == (mode != 'off')
    assert 'hello' not in str(meta)


@pytest.mark.parametrize('result', [{'model_name': 'other:evil'}, {}, [], {'model_name': ['p:strong']}])
def test_untrusted_result_rejected(monkeypatch, result):
    calls = transport(monkeypatch, result=result if result else {'model_name': None})
    selected, meta = adapter.recommend({'llmrouter_mode': 'enabled'}, [{'role':'user','content':'hello'}], [('p','strong')])
    assert selected is None
    assert meta['applied'] is False


@pytest.mark.parametrize('url', ['https://example.com/route', 'http://localhost/route', 'http://127.0.0.1@evil/route', 'http://127.0.0.1/route?key=x'])
def test_no_external_transmission(monkeypatch, url):
    calls = transport(monkeypatch)
    selected, _ = adapter.recommend({'llmrouter_mode': 'enabled', 'llmrouter_url': url}, [{'role':'user','content':'hello'}], [('p','strong')])
    assert selected is None
    assert not calls


@pytest.mark.parametrize('content', [[{'type':'image_url'}], 'x'*12001, ''])
def test_unsupported_content_stays_local(monkeypatch, content):
    calls = transport(monkeypatch)
    assert adapter.recommend({'llmrouter_mode':'enabled'}, [{'role':'user','content':content}], [('p','strong')])[0] is None
    assert not calls


def test_failure_falls_back(monkeypatch):
    transport(monkeypatch, status=503)
    selected, meta = adapter.recommend({'llmrouter_mode':'enabled'}, [{'role':'user','content':'hello'}], [('p','strong')])
    assert selected is None
    assert meta['error_type'] == 'HTTPStatusError'


def test_engine_once_per_turn_and_explicit_unchanged(engine, monkeypatch):
    monkeypatch.setitem(engine.APP_CONFIG, 'llmrouter_mode', 'enabled')
    monkeypatch.setattr(engine, '_auto_chain', [('p','cheap'), ('p','strong')])
    monkeypatch.setattr(engine, 'MODEL_NAME', 'auto')
    monkeypatch.setattr(engine, 'get_client', lambda *a: (object(), 'ignored'))
    monkeypatch.setattr(engine, '_active_model_token_limits', lambda *a: (1000000,4096))
    consulted = []
    monkeypatch.setattr(adapter, 'recommend', lambda *a: (consulted.append(a) or ('p','strong'), {'type':'routing_decision'}))
    seen = []
    def response(*a, **kw):
        seen.append(kw.get('model_override'))
        content = '✿FUNCTION✿: calculate ✿ARGS✿: {"expression":"1+1"}' if len(seen) == 1 else 'Done'
        return NS(choices=[NS(message=NS(content=content), finish_reason='stop')])
    monkeypatch.setattr(engine, '_create_completion_with_fallback', response)
    engine.run_agent([{'role':'user','content':'hello'}], max_turns=2, memory_capture=False)
    assert seen == ['strong', 'strong'] and len(consulted) == 1
    monkeypatch.setattr(engine, 'MODEL_NAME', 'explicit')
    engine.run_agent([{'role':'user','content':'hello'}], max_turns=2, memory_capture=False)
    assert len(consulted) == 1


def test_timeout_is_redacted(monkeypatch):
    monkeypatch.setenv('AGENT8088_ROUTER_TOKEN', 'test-only')
    def fail(**kwargs):
        raise httpx.ReadTimeout('secret response contents')
    monkeypatch.setattr(adapter.httpx, 'Client', fail)
    selected, meta = adapter.recommend({'llmrouter_mode':'enabled'}, [{'role':'user','content':'private query'}], [('p','m')])
    assert selected is None
    assert meta['error_type'] == 'ReadTimeout'
    assert 'secret' not in str(meta) and 'private' not in str(meta)


def test_missing_token_does_not_connect(monkeypatch):
    calls = transport(monkeypatch)
    monkeypatch.delenv('AGENT8088_ROUTER_TOKEN')
    assert adapter.recommend({'llmrouter_mode':'enabled'}, [{'role':'user','content':'hello'}], [('p','strong')])[0] is None
    assert not calls


def test_service_auth_and_selection():
    from fastapi.testclient import TestClient
    path = Path(__file__).parents[1] / 'scripts/llmrouter_service.py'
    spec = importlib.util.spec_from_file_location('router_service', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    client = TestClient(module.create_app(NS(route_single=lambda q: {'model_name':'p:strong'}), 'test-token'))
    body = {'query':'hello', 'candidates':['p:strong']}
    assert client.post('/route', json=body).status_code == 401
    response = client.post('/route', json=body, headers={'Authorization':'Bearer test-token'})
    assert response.json() == {'model_name':'p:strong'}
    body['candidates'] = ['p:cheap']
    assert client.post('/route', json=body, headers={'Authorization':'Bearer test-token'}).status_code == 422
