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
from collections import namedtuple
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
_user_cache: dict | None = None
_normalized_cache: dict | None = None


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


def _user_entries() -> dict:
    """The user's overrides alone, kept separate from the merged catalog so a
    decision can say whether a hand-written entry is what answered it."""
    global _user_cache
    if _user_cache is None:
        _user_cache = _load_json(_user_catalog_path())
    return _user_cache


def reload_catalog() -> dict:
    global _catalog_cache, _user_cache, _normalized_cache
    _catalog_cache = None
    _user_cache = None
    _normalized_cache = None
    return load_catalog()


def lookup(provider: str, model: str) -> dict | None:
    """{"rank": int, "vision": bool, "tools": bool} for an exact provider:model
    match, or None if neither the bundled nor the user catalog has it."""
    return load_catalog().get(f"{provider}:{model}")


# --- vision resolution ------------------------------------------------------

_TAG_SUFFIXES = (":free", ":latest", "-latest")

VisionDecision = namedtuple("VisionDecision", "vision source")
"""`source` is which rung of the chain answered: user, bundled, normalized,
family, probe, or default. Callers use it to say out loud when an answer was
merely the fallback -- an uncatalogued model silently getting OCR forever is
how a stale catalog stays stale."""


def _normalize_model(model: str) -> str:
    """Lowercased, with one deployment tag stripped.

    Applied to catalog keys and lookups alike, so `:free` on either side of
    the comparison stops mattering -- the bundled key
    `cohere/north-mini-code:free` and a request for `cohere/north-mini-code`
    are the same model.
    """
    name = str(model).strip().lower()
    for tag in _TAG_SUFFIXES:
        if name.endswith(tag) and len(name) > len(tag):
            return name[: -len(tag)]
    return name


def _normalized_index() -> dict:
    """provider:normalized-name -> entry, for the whole merged catalog.

    Each key is indexed under its normalized name and again under that name
    without a vendor path, so `vendorx/some-model` finds a catalog that spells
    it `some-model`. setdefault, not assignment: the first spelling of a name
    wins rather than the last, keeping the result independent of dict order.
    """
    global _normalized_cache
    if _normalized_cache is None:
        index: dict = {}
        for key, entry in load_catalog().items():
            provider, _, model = key.partition(":")
            normalized = _normalize_model(model)
            index.setdefault(f"{provider}:{normalized}", entry)
            index.setdefault(f"{provider}:{normalized.rsplit('/', 1)[-1]}", entry)
        _normalized_cache = index
    return _normalized_cache


def _family_candidates(name: str):
    """`name` with trailing -segments peeled off, longest first.

    Stops while two segments remain. One segment is not a family, it is a
    vendor prefix: allowing `gpt` to match would hand every OpenAI model the
    capabilities of whichever `gpt-*` entry happened to be indexed.
    """
    parts = name.split("-")
    for count in range(len(parts) - 1, 1, -1):
        yield "-".join(parts[:count])


def _probe(provider: str, model: str, client) -> bool | None:
    """The live probe, import kept local so this stays a leaf module."""
    try:
        from agent8088 import providers
        return providers.probe_model_vision(client, model, provider)
    except Exception:
        return None


def vision_decision(provider: str, model: str, client=None) -> VisionDecision:
    """Whether this model can read images, and what answered.

    The chain, most trustworthy first:

    1. an exact user entry -- a hand-written override outranks everything;
    2. an exact bundled entry -- a curated answer for this precise id, and a
       short circuit that keeps the 145 known pairs entirely offline;
    3. the same name normalized, then its family with version suffixes peeled
       off, so a `-002` build is not mistaken for an unknown model;
    4. the endpoint's own answer, when a client is available to ask;
    5. False.

    False last is the safe direction, unchanged from when it was the only
    answer: a vision model handed OCR text can still read it, while a
    text-only model handed image parts gets nothing usable and, on most
    providers, an error.
    """
    key = f"{provider}:{model}"

    entry = _user_entries().get(key)
    if entry is not None:
        return VisionDecision(bool(entry.get("vision")), "user")

    entry = load_catalog().get(key)
    if entry is not None:
        return VisionDecision(bool(entry.get("vision")), "bundled")

    index = _normalized_index()
    normalized = _normalize_model(model)
    bare = normalized.rsplit("/", 1)[-1]
    for candidate in (normalized, bare):
        entry = index.get(f"{provider}:{candidate}")
        if entry is not None:
            return VisionDecision(bool(entry.get("vision")), "normalized")

    for candidate in (normalized, bare):
        for family in _family_candidates(candidate):
            entry = index.get(f"{provider}:{family}")
            if entry is not None:
                return VisionDecision(bool(entry.get("vision")), "family")

    if client is not None:
        probed = _probe(provider, model, client)
        if probed is not None:
            return VisionDecision(probed, "probe")

    return VisionDecision(False, "default")


def vision_capable(provider: str, model: str, client=None) -> bool:
    """Whether this model can read images itself.

    Thin bool over vision_decision(); see it for the resolution chain. The
    two-argument call is unchanged, and stays entirely offline -- no client,
    no probe.
    """
    return vision_decision(provider, model, client=client).vision
