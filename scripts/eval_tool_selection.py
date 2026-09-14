"""Report Recall@K and COMP@K for the current tool selector. No LLM calls.

    python scripts/eval_tool_selection.py [--mode hybrid|full]

COMP@K -- the fraction of prompts whose required tools are ALL present -- is the
headline number, because a task needing four tools fails if any one of them is
missing. Per-tool Recall@K flatters the system badly: a selector can score 58%
recall while only 40% of tasks are actually completable.

Needs a working embedder (`ollama pull nomic-embed-text` plus
`memory_embed_model=nomic-embed-text` in config), or every prompt fails open to
the full catalogue and the script reports a meaningless 100%.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests" / "support"))

import agent8088.engine as E  # noqa: E402
from tool_selection_golden import GOLDEN  # noqa: E402


def score(mode):
    """Return (recall_hits, recall_total, covered, fell_open, rows)."""
    allowed = set(E.TOOL_SPECS)
    hits = total = covered = opened = 0
    rows = []
    for label, prompt, required in GOLDEN:
        E._TOOL_INDEX_CACHE.clear()
        chosen = E.select_tool_names_for_request(prompt, allowed, mode=mode)
        missing = required - chosen
        hits += len(required) - len(missing)
        total += len(required)
        covered += not missing
        opened += chosen == allowed
        rows.append((label, len(required) - len(missing), len(required), sorted(missing)))
    return hits, total, covered, opened, rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default="hybrid", choices=("hybrid", "full", "auto"))
    args = parser.parse_args()

    if E.memory.embedder() is None:
        print("WARNING: no embedder configured -- every prompt will fail open "
              "to the full catalogue and these numbers will be meaningless.\n")

    hits, total, covered, opened, rows = score(args.mode)
    for label, got, want, missing in rows:
        flag = "OK  " if not missing else "MISS"
        print(f"[{flag}] {label}: {got}/{want}"
              + (f"   MISSING {missing}" if missing else ""))

    count = len(GOLDEN)
    print(f"\nRecall@K  {hits}/{total} = {100 * hits / total:.0f}%")
    print(f"COMP@K    {covered}/{count} = {100 * covered / count:.0f}%"
          "   <- predicts task success")
    print(f"fail-open {opened}/{count}")


if __name__ == "__main__":
    main()
