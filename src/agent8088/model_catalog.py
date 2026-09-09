"""Model capability catalog: a bundled, hand-curated ranking of known
provider:model pairs, overlaid by an optional user file.

Kept as a leaf module -- no `engine` import -- so `routing.py` (also a leaf
module) can import it without creating a cycle (engine -> routing ->
model_catalog -> engine). The user-data directory is therefore resolved the
same way `providers.py` already does for its own cache file, rather than by
importing `engine._agent_data_dir()`.

Deliberately excludes context window: `providers.probe_model_context_window()`
already live-probes that per session, and `providers.model_token_limits()`'s
own docstring records that a hardcoded version was removed on purpose.
Duplicating it here would recreate the exact staleness problem this catalog
exists to fix, for a field that already has a working live answer.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

BUNDLED_CATALOG_PATH = Path(__file__).resolve().parent / "model_catalog.json"


def _user_catalog_path() -> Path:
    if os.environ.get("AGENT8088_HOME"):
        base = Path(os.environ["AGENT8088_HOME"]).expanduser()
    elif os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "agent8088"
    else:
        base = Path.home() / ".agent8088"
    return base / "model_catalog.json"


def _load_json(path: Path) -> dict:
    """A missing or malformed file is treated as empty, never raised -- a
    typo in a hand-edited user override must not crash the app."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


_catalog_cache: dict | None = None


def load_catalog() -> dict:
    """Bundled catalog overlaid by the user's, user entries winning on a
    provider:model key collision. Cached after the first call; call
    reload_catalog() to force a re-read."""
    global _catalog_cache
    if _catalog_cache is None:
        merged = _load_json(BUNDLED_CATALOG_PATH)
        merged.update(_load_json(_user_catalog_path()))
        _catalog_cache = merged
    return _catalog_cache


def reload_catalog() -> dict:
    global _catalog_cache
    _catalog_cache = None
    return load_catalog()


def lookup(provider: str, model: str) -> dict | None:
    """{"rank": int, "vision": bool, "tools": bool} for an exact provider:model
    match, or None if neither the bundled nor the user catalog has it."""
    return load_catalog().get(f"{provider}:{model}")


def vision_capable(provider: str, model: str) -> bool:
    """Whether this model can read images itself.

    An uncatalogued pair -- every local Ollama build and every custom
    OpenAI-compatible endpoint -- answers False. That is the safe direction:
    a vision model handed OCR text can still read it, while a text-only model
    handed image parts gets nothing usable and, on most providers, an error.
    Declare a local vision model in the user catalog to override.
    """
    entry = lookup(provider, model)
    return bool(entry.get("vision")) if entry else False
