"""Summarise benchmark results: success, false done, false not verified, tokens, cost, time, compactions.

    python bench/report.py <results.jsonl> [--json summary.json] [--baseline baseline.json] [--tier 1]

With --baseline, each overall number is shown next to the saved baseline's. Every report lists the runs that went
wrong with their likely cause (model, environment, harness or infra). With --tier, the results are checked against that
tier's pass bar in bench/tiers.json.
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


def causes(results: list) -> str:
    """The runs worth reading, each with who it likely belongs to: the model, the environment, the harness or infra.

    A failed task is not always the harness's fault: the model can get the work wrong and the harness rightly say so.
    Only a wrong verdict or a missing answer is the harness's to fix; the rest is the model's or the setting's."""
    lines = ["\nruns to read (likely cause, from the outcome and the answer's reasons; the trace decides):"]
    counts: dict = {}
    for result in results:
        who, why = benchlib.likely_cause(result)
        counts[who] = counts.get(who, 0) + 1
        if who != "none":
            name = f"{result.get('task')} {str(result.get('started', ''))[11:16]}"
            lines.append(f"  {who:<11} {benchlib.outcome(result):<19} {name:<28} {why}")
    if len(lines) == 1:
        lines.append("  none")
    lines.append("  by cause: " + ", ".join(f"{who} {n}" for who, n in sorted(counts.items())))
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("results")
    parser.add_argument("--json", help="write the summary here")
    parser.add_argument("--baseline", help="a summary JSON saved earlier, to compare against")
    parser.add_argument("--tier", help="check the results against this tier's pass bar (bench/tiers.json)")
    args = parser.parse_args(argv)
    results = load(args.results)
    summary = benchlib.aggregate(results)
    times = sorted(r["started"] for r in results if r.get("started"))
    ends = sorted(r["ended"] for r in results if r.get("ended"))
    if times and ends:
        print(f"runs from {benchlib.clock(times[0])} to {benchlib.clock(ends[-1])}")
    print(table(summary))
    baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8")) if args.baseline else None
    if baseline:
        base = baseline["overall"]
        print("\ncompared with the baseline (now / then):")
        for key, title, form in COLUMNS:
            print(f"  {title:<18} {_cell(summary['overall'].get(key), form):>10} / {_cell(base.get(key), form)}")
    print(causes(results))
    if args.tier:
        tier = benchlib.load_tier(args.tier)
        compared, added = benchlib.comparable(results, baseline) if baseline else (None, [])
        rows = benchlib.tier_check(summary, tier.get("bar", {}), baseline, compared=compared)
        print(f"\n{tier.get('title') or 'Tier ' + args.tier}:")
        if added:
            print(f"  compared with the baseline on its own tasks only; new since then: {', '.join(added)} "
                  "(save this run's summary as the next baseline)")
        for rule, passed, detail in rows:
            print(f"  {'pass' if passed else 'FAIL'}  {rule}: {detail}")
        if not baseline:
            print("  (no --baseline: the rules that compare with one were not checked)")
        print("  result: " + ("passed" if all(p for _r, p, _d in rows) else "NOT passed"))
    if args.json:
        Path(args.json).write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
