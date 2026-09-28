"""Memory hooks for browser browsing preferences and session recall."""
from __future__ import annotations

import logging
from typing import Any, List
from urllib.parse import urlparse

_log = logging.getLogger(__name__)


def get_active_memory() -> Any:
    """Retrieve active memory store from agent8088.memory."""
    try:
        from agent8088 import memory
        return memory
    except Exception as e:
        _log.debug("Memory module not available: %s", e)
        return None


def extract_domain(url: str) -> str:
    """Extract the netloc domain from a URL."""
    try:
        parsed = urlparse(url)
        return parsed.netloc.lower() or ""
    except Exception:
        return ""


def recall_browser_domain_preferences(domain: str) -> List[str]:
    """Recall stored user browsing preferences for a domain."""
    if not domain:
        return []
    mem = get_active_memory()
    if mem is None:
        return []

    try:
        query = f"browsing preference {domain}"
        results = mem.recall(query) if hasattr(mem, "recall") else []
        return [str(r) for r in results if r]
    except Exception as e:
        _log.debug("Failed recalling browsing preferences for %s: %s", domain, e)
        return []


def record_browser_preference(domain: str, preference: str) -> None:
    """Record a user browsing preference into active memory."""
    if not domain or not preference:
        return
    mem = get_active_memory()
    if mem is None:
        return

    try:
        fact = f"User browsing preference for {domain}: {preference.strip()}"
        if hasattr(mem, "store"):
            mem.store(fact)
            _log.info("Saved browsing preference to memory: %s", fact)
    except Exception as e:
        _log.debug("Failed storing browsing preference for %s: %s", domain, e)
