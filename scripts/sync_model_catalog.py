"""One-shot: convert freeLLMAPI's monthly catalog feed into Agent8088's
model_catalog.json, for the providers Agent8088 actually has.

freeLLMAPI schema:  intelligenceRank (lower = stronger, 1-2 = frontier),
supportsVision, supportsTools, enabled, quirks, platform:modelId.
Agent8088 schema:   rank 0-100 (higher = stronger), vision, tools.

Rank inversion:  freeLLMAPI ranks 1..N are ordinal (rank 1 = strongest).
Mapped to 0-100 linearly:  ours = round((maxRank - theirs) / (maxRank - 1) * 95) + 5
so their #1 -> 100 and the tail compresses toward 5. Ties in their ordinal
get the same score, which is fine: order_for_seed breaks ties by discovery
order anyway.

Custom OpenAI-compatible endpoints ("custom:<model>") are the edge case:
freeLLMAPI deliberately excludes custom endpoints from its catalog ("user-
supplied endpoint, nothing to publish"), so the catalog can never know what
a custom:xyz is. Handled by NOT guessing: rank_hint's name-based fallback
(param count + keywords) is what runs for custom models, and the user can
pin a rank via the user overlay (~/.agent8088/model_catalog.json,
{"custom:<model>": {"rank": 80, ...}}). The custom provider is registered
per-session by cli.py cmd_models, so it has no fixed provider list to ship.
"""
import json
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "agent8088"
CATALOG_PATH = SRC / "model_catalog.json"
FEED_PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(r"C:\Users\ADMINI~1\AppData\Local\Temp\freellm_catalog.json")

# freeLLMAPI platform id -> Agent8088 provider id.
PLATFORM_MAP = {
    "google": "gemini",
    "groq": "groq",
    "mistral": "mistral",
    "ollama": "ollama",
    "openrouter": "openrouter",
    # zhipu/nvidia/cohere/cloudflare/huggingface/modelscope etc. have no
    # Agent8088 provider today; extend this map when a new provider lands.
}

# Providers with no freeLLMAPI platform: keep the hand-curated entries only.
NO_FEED_PROVIDERS = {
    "openai", "anthropic", "deepseek", "moonshot", "qwen",
    "ollama-cloud", "cerebras",
}


def convert(feed: dict) -> dict:
    models = [m for m in feed["models"] if m.get("enabled", True)]
    models = [m for m in models if m["platform"] in PLATFORM_MAP]

    # freeLLMAPI's ranks are ordinal ints; find the worst rank present.
    max_rank = max(int(m["intelligenceRank"]) for m in models)
    min_rank = min(int(m["intelligenceRank"]) for m in models)
    span = max(max_rank - min_rank, 1)

    out = {}
    for m in models:
        theirs = int(m["intelligenceRank"])
        # Linear inversion, clamped to [5, 100]: their best (min_rank) -> 100,
        # their worst -> 5, everything else in between.
        ours = round(100 - (theirs - min_rank) / span * 95)
        ours = max(5, min(100, ours))
        key = f"{PLATFORM_MAP[m['platform']]}:{m['modelId']}"
        out[key] = {
            "rank": ours,
            "vision": bool(m.get("supportsVision", False)),
            "tools": bool(m.get("supportsTools", False)),
        }
        # contextWindow deliberately excluded: live-probed per session.

    return out


def main() -> None:
    feed = json.loads(FEED_PATH.read_text(encoding="utf-8"))
    converted = convert(feed)

    # Merge over the current hand-curated catalog: freeLLMAPI data wins for
    # the platforms it covers (it's maintained; ours was a snapshot), but
    # never deletes entries for providers the feed doesn't know.
    existing = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    existing.update(converted)
    merged = dict(sorted(existing.items()))

    CATALOG_PATH.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    print(f"bundled entries: {len(existing)} -> {len(merged)} "
          f"({len(converted)} from the freeLLMAPI {feed.get('version')} feed)")


if __name__ == "__main__":
    main()