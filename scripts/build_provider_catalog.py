"""Rebuild src/agent8088/model_catalog.json with EVERY model our providers offer.

Sources, merged in priority order (first hit wins):
  1. live API list (gemini, ollama-cloud - wherever a key works)
  2. freeLLMAPI reference catalog (5 mapped platforms, free tiers)
  3. FALLBACK_MODELS from providers.py
  4. previous bundled catalog entries (never dropped)

Rank/vision/tools resolution (first hit wins):
  1. freeLLMAPI intelligenceRank, inverted to 0-100 + supportsVision/Tools
  2. previous bundled entry (hand-curated values preserved)
  3. routing.rank_hint name heuristic for rank; vision/tools unknown -> false/true
     defaults, seeded from the reference when the same family appears there.

Output: compact one-line style, grouped by provider with blank separators.
"""
import json
import sys
from collections import OrderedDict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from agent8088.routing import rank_hint  # noqa: E402
from agent8088.providers import FALLBACK_MODELS  # noqa: E402

REF_PATH = REPO / "docs" / "reference" / "freellm_catalog_2026.09.07.json"
CATALOG_PATH = REPO / "src" / "agent8088" / "model_catalog.json"

FREELLM_PLATFORMS = {
    "google": "gemini",
    "groq": "groq",
    "mistral": "mistral",
    "ollama": "ollama",
    "openrouter": "openrouter",
}

# provider -> (api key env, models endpoint). Only providers with a working
# key contribute API-sourced names; others fall through the sources below.
API_SOURCES = {
    "gemini":       ("GEMINI_API_KEY",   "https://generativelanguage.googleapis.com/v1beta/openai/models"),
    "ollama-cloud": ("OLLAMA_API_KEY",   "https://ollama.com/v1/models"),
    "openrouter":   ("OPENROUTER_API_KEY", "https://openrouter.ai/api/v1/models"),
}


def fetch_api_models(provider: str, url: str):
    import os
    import urllib.request
    key = os.environ.get(API_SOURCES[provider][0], "").strip()
    if not key or key.startswith("YOUR_"):
        return None
    try:
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}",
                                                   "User-Agent": "agent8088-catalog-sync"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        ids = [m.get("id", "") for m in data.get("data", [])]
        ids = [i[len("models/"):] if i.startswith("models/") else i for i in ids if i]
        return sorted(set(ids)) or None
    except Exception:  # noqa: BLE001 - dead key falls through to other sources
        return None


def main() -> None:
    ref = json.loads(REF_PATH.read_text(encoding="utf-8"))
    ref_by_key = {f"{m['platform']}:{m['modelId']}": m for m in ref["models"]}
    ref_for_us = {}
    for platform, provider in FREELLM_PLATFORMS.items():
        for m in ref["models"]:
            if m["platform"] == platform:
                ref_for_us[f"{provider}:{m['modelId']}"] = m
    ranks = [int(m["intelligenceRank"]) for m in ref["models"]]
    min_rank, max_rank = min(ranks), max(ranks)
    span = max(max_rank - min_rank, 1)

    def inverted(theirs: int) -> int:
        return max(5, min(100, round(100 - (theirs - min_rank) / span * 95)))

    # name sets per provider, unioned across sources
    names = {p: set() for p in set(FREELLM_PLATFORMS.values()) | set(API_SOURCES) | set(FALLBACK_MODELS)}
    for provider in FALLBACK_MODELS:
        names.setdefault(provider, set()).update(FALLBACK_MODELS[provider])
    for key in ref_for_us:
        names.setdefault(key.split(":", 1)[0], set()).add(key.split(":", 1)[1])
    for provider, (_, url) in API_SOURCES.items():
        api_ids = fetch_api_models(provider, url) or []
        names.setdefault(provider, set()).update(api_ids)

    # previous catalog: values preserved, nothing dropped
    previous = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    for key in previous:
        names.setdefault(key.split(":", 1)[0], set()).add(key.split(":", 1)[1])

    out = {}
    stats = {"freellm": 0, "previous": 0, "heuristic": 0}
    for provider, models in sorted(names.items()):
        for model_id in sorted(models):
            key = f"{provider}:{model_id}"
            ref_entry = ref_for_us.get(key)
            if ref_entry:
                vision, tools = bool(ref_entry.get("supportsVision")), bool(ref_entry.get("supportsTools"))
                rank = inverted(int(ref_entry["intelligenceRank"]))
                stats["freellm"] += 1
            elif key in previous:
                prev = previous[key]
                rank, vision, tools = int(prev["rank"]), bool(prev["vision"]), bool(prev["tools"])
                stats["previous"] += 1
            else:
                # unknown to every source: heuristic from the name, honest defaults.
                rank = rank_hint(provider, model_id)
                vision, tools = False, True
                stats["heuristic"] += 1
            out[key] = {"rank": rank, "vision": vision, "tools": tools}

    lines = ["{"]
    groups = OrderedDict()
    for key in sorted(out):
        provider, _ = key.split(":", 1)
        groups.setdefault(provider, []).append(key)
    for i, (provider, keys) in enumerate(groups.items()):
        if i:
            lines.append("")
        for key in keys:
            e = out[key]
            lines.append(f'  "{key}": {{"rank": {e["rank"]}, "vision": {str(e["vision"]).lower()}, '
                         f'"tools": {str(e["tools"]).lower()}}},')
    lines[-1] = lines[-1].rstrip(",")
    lines.append("}")
    CATALOG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"wrote {len(out)} entries across {len(groups)} providers "
          f"(rank source: {stats['freellm']} freellm / {stats['previous']} previous / "
          f"{stats['heuristic']} heuristic)")


if __name__ == "__main__":
    main()