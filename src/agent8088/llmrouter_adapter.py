"""Optional, selection-only LLMRouter client. No provider credentials or execution."""
import os
import time
from urllib.parse import urlsplit

import httpx

# A refused connection is not free: Windows retransmits the SYN before giving
# up, so a stopped service costs seconds per turn rather than microseconds.
# Same reasoning as the memory embedder's breaker -- a service that is down
# fails identically every time, so learning that once is enough.
_BREAKER_SECONDS = 60
_failed_at = 0.0


class _NotRoutable(Exception):
    """This request is not one we route, decided locally.

    Distinct from a service failure on purpose. A multimodal turn or an
    oversized query says nothing about the router's health, so it must not
    latch the breaker -- one screenshot would otherwise silence routing for
    every ordinary turn in the next minute.
    """


def reset_breaker() -> None:
    """Forget a recorded failure. For tests and for a deliberate re-probe."""
    global _failed_at
    _failed_at = 0.0


def recommend(config, messages, candidates):
    """Return (candidate or None, metadata). Fail closed to the existing ladder.

    candidates is an operator-approved, already context/health-filtered list of
    (provider, model) pairs. Never accept a new provider from the service.
    """
    global _failed_at
    started = time.monotonic()
    mode = str(config.get('llmrouter_mode', 'off')).lower()
    meta = {'type': 'routing_decision', 'backend': 'llmrouter', 'mode': mode}
    if mode not in ('shadow', 'enabled'):
        return None, dict(meta, reason='disabled')
    breaker = float(config.get('llmrouter_breaker_seconds', _BREAKER_SECONDS))
    if _failed_at and (time.monotonic() - _failed_at) < breaker:
        return None, dict(meta, reason='router_unavailable_recently',
                          applied=False, latency_ms=0)
    try:
        url = str(config.get('llmrouter_url', 'http://127.0.0.1:8191/route'))
        parsed = urlsplit(url)
        if (parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', '::1')
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ValueError('loopback URL required')
        token = os.environ.get(str(config.get('llmrouter_token_env', 'AGENT8088_ROUTER_TOKEN')), '')
        if not token:
            raise ValueError('router token missing')
        timeout = float(config.get('llmrouter_timeout_seconds', '2'))
        if not 0 < timeout <= 10:
            raise ValueError('invalid timeout')
        candidates = list(dict.fromkeys(candidates))
        if not candidates or len(candidates) > 64:
            raise _NotRoutable('no eligible candidates or too many candidates')
        # No raw attachments, tool results, system instructions or whole history.
        content = next((m.get('content') for m in reversed(messages) if m.get('role') == 'user'), '')
        if not isinstance(content, str) or not content.strip() or len(content) > 12000:
            raise _NotRoutable('unsupported or oversized query')
        ids = {f'{p}:{m}': (p, m) for p, m in candidates}
        with httpx.Client(timeout=timeout, trust_env=False, follow_redirects=False) as client:
            with client.stream('POST', url, headers={'Authorization': f'Bearer {token}'},
                               json={'query': content, 'candidates': list(ids)}) as response:
                response.raise_for_status()
                data = bytearray()
                for chunk in response.iter_bytes():
                    if time.monotonic() - started > timeout:
                        raise TimeoutError('response deadline exceeded')
                    data.extend(chunk)
                    if len(data) > 8192:
                        raise ValueError('oversized response')
        import json
        result = json.loads(data)
        selected = result.get('model_name') if isinstance(result, dict) else None
        if selected not in ids:
            raise ValueError('router selected an ineligible model')
        meta.update(reason='selected', selected=selected,
                    applied=mode == 'enabled')
        _failed_at = 0.0
        return (ids[selected] if mode == 'enabled' else None), meta
    except _NotRoutable as exc:
        # Local refusal: the breaker stays open, the next turn still asks.
        meta.update(reason='not_routable', error_type=type(exc).__name__, applied=False)
        return None, meta
    except Exception as exc:
        _failed_at = time.monotonic()
        # Never log response bodies, input text, tokens or exception messages.
        meta.update(reason='router_unavailable_or_invalid', error_type=type(exc).__name__, applied=False)
        return None, meta
    finally:
        meta['latency_ms'] = round((time.monotonic() - started) * 1000)
