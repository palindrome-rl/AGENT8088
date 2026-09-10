"""Live rate-limit/usage indicator: header parsing for Tier-1 providers
(Anthropic, OpenAI, Groq, Cerebras), the raw-response gating in
_create_completion that only Tier-1 providers pay for, and the OpenRouter
usage poller. See providers.RATE_LIMIT_HEADERS for the tier list.
"""
import urllib.request  # noqa: F401  ensures urllib.request is attached to the
                        # shared urllib package object before tests patch
                        # engine.urllib.request (_poll_once imports it lazily)
from unittest.mock import MagicMock

import pytest


# --- header parser -----------------------------------------------------

def test_anthropic_headers_parsed(engine):
    result = engine._parse_rate_limit_headers("anthropic", {
        "anthropic-ratelimit-tokens-remaining": "4000",
        "anthropic-ratelimit-tokens-limit": "5000",
    })
    assert result == {"provider": "anthropic", "pct_remaining": 80, "balance": None}


def test_openai_headers_parsed(engine):
    result = engine._parse_rate_limit_headers("openai", {
        "x-ratelimit-remaining-requests": "45",
        "x-ratelimit-limit-requests": "50",
        "x-ratelimit-remaining-tokens": "9000",
        "x-ratelimit-limit-tokens": "10000",
    })
    # min of the two ratios (90% requests, 90% tokens) -- both equal here
    assert result == {"provider": "openai", "pct_remaining": 90, "balance": None}


def test_takes_the_more_constrained_of_requests_and_tokens(engine):
    """If tokens are nearly exhausted but requests aren't, the tighter
    constraint should win -- that's the one about to actually block."""
    result = engine._parse_rate_limit_headers("groq", {
        "x-ratelimit-remaining-requests": "45",
        "x-ratelimit-limit-requests": "50",
        "x-ratelimit-remaining-tokens": "100",
        "x-ratelimit-limit-tokens": "10000",
    })
    assert result == {"provider": "groq", "pct_remaining": 1, "balance": None}


def test_cerebras_treated_as_tokens_only(engine):
    """Cerebras's docs disagreed on whether requests/tokens get separate
    headers -- until verified live, only the single triplet is trusted."""
    result = engine._parse_rate_limit_headers("cerebras", {
        "x-ratelimit-remaining": "30",
        "x-ratelimit-limit": "60",
    })
    assert result == {"provider": "cerebras", "pct_remaining": 50, "balance": None}


def test_missing_headers_returns_none(engine):
    assert engine._parse_rate_limit_headers("anthropic", {}) is None


def test_malformed_header_value_returns_none_not_raise(engine):
    assert engine._parse_rate_limit_headers("anthropic", {
        "anthropic-ratelimit-tokens-remaining": "not-a-number",
        "anthropic-ratelimit-tokens-limit": "5000",
    }) is None


def test_unknown_provider_returns_none(engine):
    assert engine._parse_rate_limit_headers("ollama", {"x-anything": "1"}) is None


# --- _create_completion gating ------------------------------------------

def _fake_completion():
    completion = MagicMock()
    completion.choices = []
    return completion


def test_tier1_provider_uses_raw_response_and_sets_status(engine, monkeypatch):
    monkeypatch.setattr(engine, "_last_rate_limit_status", None)
    client = MagicMock()
    raw = MagicMock()
    raw.headers = {
        "anthropic-ratelimit-tokens-remaining": "10",
        "anthropic-ratelimit-tokens-limit": "100",
    }
    raw.parse.return_value = _fake_completion()
    client.chat.completions.with_raw_response.create.return_value = raw

    engine._create_completion(client, [{"role": "user", "content": "hi"}], [],
                               provider_name="anthropic")

    client.chat.completions.with_raw_response.create.assert_called_once()
    client.chat.completions.create.assert_not_called()
    assert engine._last_rate_limit_status == {"provider": "anthropic", "pct_remaining": 10, "balance": None}


def test_tier1_provider_streaming_uses_streaming_response_and_sets_status(engine, monkeypatch):
    """The interactive on_token path -- must use with_streaming_response, not
    with_raw_response, or true incremental streaming breaks (see comment at
    the call site)."""
    monkeypatch.setattr(engine, "_last_rate_limit_status", None)
    client = MagicMock()
    raw = MagicMock()
    raw.headers = {
        "anthropic-ratelimit-tokens-remaining": "30",
        "anthropic-ratelimit-tokens-limit": "100",
    }
    raw.parse.return_value = iter([])  # no chunks -- just verify header capture + cleanup
    stream_ctx = MagicMock()
    stream_ctx.__enter__.return_value = raw
    stream_ctx.__exit__.return_value = False
    client.chat.completions.with_streaming_response.create.return_value = stream_ctx

    engine._create_completion(client, [{"role": "user", "content": "hi"}], [],
                               provider_name="anthropic", on_token=lambda *_: None)

    client.chat.completions.with_streaming_response.create.assert_called_once()
    client.chat.completions.create.assert_not_called()
    assert engine._last_rate_limit_status == {"provider": "anthropic", "pct_remaining": 30, "balance": None}
    stream_ctx.__exit__.assert_called_once()  # connection cleaned up


def test_non_tier1_provider_uses_plain_create(engine, monkeypatch):
    monkeypatch.setattr(engine, "_last_rate_limit_status", None)
    client = MagicMock()
    client.chat.completions.create.return_value = _fake_completion()

    engine._create_completion(client, [{"role": "user", "content": "hi"}], [],
                               provider_name="ollama")

    client.chat.completions.create.assert_called_once()
    client.chat.completions.with_raw_response.create.assert_not_called()
    assert engine._last_rate_limit_status is None


# --- OpenRouter poller ---------------------------------------------------

def test_openrouter_poll_success_sets_status(engine, monkeypatch):
    monkeypatch.setattr(engine, "_last_rate_limit_status", None)
    monkeypatch.setitem(engine.PROVIDERS, "openrouter", {"api_key": "sk-test"})
    poller = engine._OpenRouterUsagePoller()

    fake_resp = MagicMock()
    fake_resp.read.return_value = b'{"data": {"limit": 1000, "limit_remaining": 250}}'
    fake_resp.__enter__.return_value = fake_resp
    fake_resp.__exit__.return_value = False
    monkeypatch.setattr(engine.urllib.request, "urlopen", lambda *a, **k: fake_resp)

    poller._poll_once()

    assert engine._last_rate_limit_status == {"provider": "openrouter", "pct_remaining": 25, "balance": None}


def test_openrouter_poll_unlimited_leaves_status_unset(engine, monkeypatch):
    monkeypatch.setattr(engine, "_last_rate_limit_status", None)
    monkeypatch.setitem(engine.PROVIDERS, "openrouter", {"api_key": "sk-test"})
    poller = engine._OpenRouterUsagePoller()

    fake_resp = MagicMock()
    fake_resp.read.return_value = b'{"data": {"limit": null, "limit_remaining": null}}'
    fake_resp.__enter__.return_value = fake_resp
    fake_resp.__exit__.return_value = False
    monkeypatch.setattr(engine.urllib.request, "urlopen", lambda *a, **k: fake_resp)

    poller._poll_once()

    assert engine._last_rate_limit_status is None


def test_openrouter_poll_network_failure_never_raises(engine, monkeypatch):
    monkeypatch.setattr(engine, "_last_rate_limit_status", None)
    monkeypatch.setitem(engine.PROVIDERS, "openrouter", {"api_key": "sk-test"})
    poller = engine._OpenRouterUsagePoller()

    def _boom(*a, **k):
        raise OSError("network down")

    monkeypatch.setattr(engine.urllib.request, "urlopen", _boom)

    poller._poll_once()  # must not raise

    assert engine._last_rate_limit_status is None


def test_openrouter_poll_skipped_without_api_key(engine, monkeypatch):
    monkeypatch.setattr(engine, "_last_rate_limit_status", None)
    monkeypatch.setitem(engine.PROVIDERS, "openrouter", {})
    poller = engine._OpenRouterUsagePoller()

    called = []
    monkeypatch.setattr(engine.urllib.request, "urlopen", lambda *a, **k: called.append(1))

    poller._poll_once()

    assert called == []
    assert engine._last_rate_limit_status is None


# --- provider scoping ----------------------------------------------------
# Regression: the status is one module global but a session switches
# providers freely. A percentage captured from Groq was still being shown
# after switching to a custom endpoint and to OpenRouter -- a real number
# under the wrong provider's name, which is worse than showing nothing.

def test_status_is_returned_for_the_provider_it_came_from(engine, monkeypatch):
    monkeypatch.setattr(engine, "_last_rate_limit_status",
                        {"provider": "groq", "pct_remaining": 84, "balance": None})
    assert engine.current_rate_limit_status("groq")["pct_remaining"] == 84


def test_status_is_hidden_under_a_different_provider(engine, monkeypatch):
    monkeypatch.setattr(engine, "_last_rate_limit_status",
                        {"provider": "groq", "pct_remaining": 84, "balance": None})
    assert engine.current_rate_limit_status("custom") is None
    assert engine.current_rate_limit_status("openrouter") is None
    assert engine.current_rate_limit_status("ollama") is None


def test_status_hidden_when_no_active_provider(engine, monkeypatch):
    monkeypatch.setattr(engine, "_last_rate_limit_status",
                        {"provider": "groq", "pct_remaining": 84, "balance": None})
    assert engine.current_rate_limit_status("") is None


def test_status_bar_omits_segment_for_a_provider_without_data(engine, monkeypatch):
    """End-to-end: the rendered bar itself must not carry another
    provider's number."""
    from agent8088 import cli
    monkeypatch.setattr(engine, "_last_rate_limit_status",
                        {"provider": "groq", "pct_remaining": 84, "balance": None})
    monkeypatch.setattr(cli, "_active_provider_name", lambda: "custom")
    assert "84" not in "".join(t for _, t in cli._status_bar_fragments())
    monkeypatch.setattr(cli, "_active_provider_name", lambda: "groq")
    assert "84" in "".join(t for _, t in cli._status_bar_fragments())
