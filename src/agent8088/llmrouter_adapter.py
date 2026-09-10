"""Optional, selection-only LLMRouter client. No provider credentials or execution."""
import os
import time
from urllib.parse import urlsplit

import httpx


def recommend(config, messages, candidates):
    """Return (candidate or None, metadata). Fail closed to the existing ladder.

    candidates is an operator-approved, already context/health-filtered list of
    (provider, model) pairs. Never accept a new provider from the service.
    """
    started = time.monotonic()
    mode = str(config.get('llmrouter_mode', 'off')).lower()
    meta = {'type': 'routing_decision', 'backend': 'llmrouter', 'mode': mode}
    if mode not in ('shadow', 'enabled'):
        return None, dict(meta, reason='disabled')
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
            raise ValueError('no eligible candidates or too many candidates')
        # No raw attachments, tool results, system instructions or whole history.
        content = next((m.get('content') for m in reversed(messages) if m.get('role') == 'user'), '')
        if not isinstance(content, str) or not content.strip() or len(content) > 12000:
            raise ValueError('unsupported or oversized query')
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
        return (ids[selected] if mode == 'enabled' else None), meta
    except Exception as exc:
        # Never log response bodies, input text, tokens or exception messages.
        meta.update(reason='router_unavailable_or_invalid', error_type=type(exc).__name__, applied=False)
        return None, meta
    finally:
        meta['latency_ms'] = round((time.monotonic() - started) * 1000)
