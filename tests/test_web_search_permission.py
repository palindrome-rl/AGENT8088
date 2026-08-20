"""engine.py's web_search permission gate.

ddgs is the keyless backend every install ships with. When it is the only
backend the chain would actually try (no SearXNG, no keyed Tavily/Exa), the
tool must not need an interactive escalation — that gate had no session-wide
grant (see grant_escalation), so a model issuing more than one search per
turn would get its approvals clobbered and web_search would never complete.
A configured SearXNG or keyed backend still goes through the normal
escalation flow: only the backend nobody has to opt into is exempt.
"""
import json

import pytest


@pytest.fixture(autouse=True)
def _no_real_env_file(monkeypatch, engine):
    """Isolate from whatever real .env/secrets happen to sit next to this repo."""
    monkeypatch.setattr(engine, "load_env_file", lambda path=None: {})
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("EXA_API_KEY", raising=False)


def test_ddgs_only_chain_is_true_with_no_other_backend_configured(engine):
    assert engine._ddgs_only_chain() is True


def test_ddgs_only_chain_is_false_once_searxng_is_configured(engine):
    engine.APP_CONFIG["search_base_url"] = "http://localhost:8080/search?q="
    engine.SEARCH_BASE_URL_CONFIGURED = True
    assert engine._ddgs_only_chain() is False


def test_ddgs_only_chain_is_false_once_a_keyed_backend_is_configured(engine, monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    assert engine._ddgs_only_chain() is False


def test_web_search_skips_escalation_when_ddgs_is_the_only_backend(engine, monkeypatch):
    calls = []

    def fake_run_search(query, limit, registry, config, context, **kwargs):
        calls.append(query)
        return "Search results (via ddgs):\n\n1. t\n   https://e.test\n"

    monkeypatch.setattr(engine.web_search, "run_search", fake_run_search)
    result = engine.exec_tool("web_search", json.dumps({"query": "test query"}))
    assert not result.startswith("ESCALATION_REQUEST\x1f")
    assert calls == ["test query"]


def test_web_search_still_escalates_when_searxng_is_configured(engine):
    engine.APP_CONFIG["search_base_url"] = "http://localhost:8080/search?q="
    engine.SEARCH_BASE_URL_CONFIGURED = True
    result = engine.exec_tool("web_search", json.dumps({"query": "test query"}))
    assert result.startswith("ESCALATION_REQUEST\x1f")
    assert result.split("\x1f")[2] == "network_request"


def test_web_search_still_escalates_when_a_keyed_backend_is_configured(engine, monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    result = engine.exec_tool("web_search", json.dumps({"query": "test query"}))
    assert result.startswith("ESCALATION_REQUEST\x1f")


def test_multiple_ddgs_searches_in_a_row_never_need_approval(engine, monkeypatch):
    """The bug this closes: two web_search calls in the same turn used to
    fight over the single one-shot approval slot, so approving one silently
    lost the other's grant and the search never completed."""
    def fake_run_search(query, limit, registry, config, context, **kwargs):
        return f"Search results (via ddgs):\n\n1. {query}\n   https://e.test\n"

    monkeypatch.setattr(engine.web_search, "run_search", fake_run_search)
    first = engine.exec_tool("web_search", json.dumps({"query": "query one"}))
    second = engine.exec_tool("web_search", json.dumps({"query": "query two"}))
    assert not first.startswith("ESCALATION_REQUEST\x1f")
    assert not second.startswith("ESCALATION_REQUEST\x1f")
