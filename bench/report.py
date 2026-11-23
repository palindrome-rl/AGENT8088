"""Summarise benchmark results: success, false done, false not verified, tokens, cost, time, compactions.

    python bench/report.py <results.jsonl> [--json summary.json] [--baseline baseline.json]

With --baseline, each overall number is shown next to the saved baseline's.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import benchlib

COLUMNS = (
    ("runs", "runs", "{}"),
    ("success_rate", "success", "pct"),
    ("mean_score", "score", "{:.2f}"),
    ("false_done_rate", "false done", "pct"),
    ("false_not_verified_rate", "false NV", "pct"),
    ("no_answer_rate", "no answer", "pct"),
    ("input_tokens", "tokens in", "{:,}"),
    ("output_tokens", "tokens out", "{:,}"),
    ("cost_usd", "cost $", "{:.2f}"),
    ("elapsed_sec", "minutes", "min"),
    ("compactions", "compact.", "{}"),
    ("success_after_compaction", "ok after compact.", "{}"),
    ("recoveries", "recoveries", "{}"),
)


def _cell(value, form: str) -> str:
    if value is None:
        return "-"
    if form == "pct":
        return f"{value * 100:.0f}%"
    if form == "min":
        return f"{value / 60:.1f}"
    return form.format(value)


def load(path) -> list:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def table(summary: dict) -> str:
    rows = [("group",) + tuple(title for _, title, _ in COLUMNS)]
    for name, group in list(summary["groups"].items()) + [("ALL", summary["overall"])]:
        rows.append((name,) + tuple(_cell(group.get(key), form) for key, _, form in COLUMNS))
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    return "\n".join("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)) for row in rows)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("results")
    parser.add_argument("--json", help="write the summary here")
    parser.add_argument("--baseline", help="a summary JSON saved earlier, to compare against")
    args = parser.parse_args(argv)
    results = load(args.results)
    summary = benchlib.aggregate(results)
    times = sorted(r["started"] for r in results if r.get("started"))
    ends = sorted(r["ended"] for r in results if r.get("ended"))
    if times and ends:
        print(f"runs from {benchlib.clock(times[0])} to {benchlib.clock(ends[-1])}")
    print(table(summary))
    if args.baseline:
        base = json.loads(Path(args.baseline).read_text(encoding="utf-8"))["overall"]
        print("\ncompared with the baseline (now / then):")
        for key, title, form in COLUMNS:
            print(f"  {title:<18} {_cell(summary['overall'].get(key), form):>10} / {_cell(base.get(key), form)}")
    if args.json:
        Path(args.json).write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
