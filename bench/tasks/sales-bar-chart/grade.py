"""Grader for sales-bar-chart, the small visual task.

    python grade.py <workspace> --json <report.json>

Opens chart.html from disk in headless Chromium at 800 by 600 with every outside request blocked, and measures what
is on screen: where each bar is, how tall, what colour, and where each label sits. A chart that "looks done" in a
description but has bars out of proportion, labels piled on each other or a page wider than the window fails. When
no browser can be started the report has no "passed" field, so the run counts as ungraded, not failed.
"""
import argparse
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))  # bench/, for graderlib
import graderlib

SALES = {"Jan": 120, "Feb": 95, "Mar": 140, "Apr": 80, "May": 160, "Jun": 110}
BEST = max(SALES, key=SALES.get)
TOTAL = 10
WIDTH, HEIGHT = 800, 600

MEASURE = """() => {
  const box = r => ({left: r.left, right: r.right, top: r.top, bottom: r.bottom, width: r.width, height: r.height});
  const bars = [...document.querySelectorAll('[data-month]')].map(el => {
    const style = getComputedStyle(el);
    return {name: String(el.dataset.month).trim(), ...box(el.getBoundingClientRect()),
            colour: (style.fill || '') + '|' + (style.backgroundColor || '')};
  });
  const texts = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const node = walker.currentNode, text = node.textContent.trim();
    if (!text || !node.parentElement) continue;
    const style = getComputedStyle(node.parentElement);
    if (style.visibility === 'hidden' || style.display === 'none' || Number(style.opacity) === 0) continue;
    let rect;
    if (node.parentElement instanceof SVGElement) { rect = node.parentElement.getBoundingClientRect(); }
    else { const range = document.createRange(); range.selectNodeContents(node); rect = range.getBoundingClientRect(); }
    if (rect.width > 0 && rect.height > 0) texts.push({text, ...box(rect)});
  }
  const root = document.documentElement;
  return {bars, texts, scrollWidth: Math.max(root.scrollWidth, document.body.scrollWidth),
          scrollHeight: Math.max(root.scrollHeight, document.body.scrollHeight)};
}"""


def _centre(rect) -> float:
    return (rect["left"] + rect["right"]) / 2


def _overlap(a, b) -> float:
    width = min(a["right"], b["right"]) - max(a["left"], b["left"])
    height = min(a["bottom"], b["bottom"]) - max(a["top"], b["top"])
    return width * height if width > 0 and height > 0 else 0.0


def _label(texts, want: str, bar, below: bool):
    """The text equal to `want` that sits under (or over) the bar, if any."""
    for text in texts:
        if text["text"].lower() != want.lower():
            continue
        if not bar["left"] - 6 <= _centre(text) <= bar["right"] + 6:
            continue
        if below and text["top"] >= bar["bottom"] - 2:
            return text
        if not below and text["bottom"] <= bar["top"] + 4:
            return text
    return None


def judge(seen: dict, checks: dict, errors: list, outside: list):
    bars = sorted(seen["bars"], key=_centre)
    by_name = {bar["name"]: bar for bar in bars}
    checks["six bars, one per month, in order"] = [bar["name"] for bar in bars] == list(SALES)
    if set(by_name) != set(SALES):
        return
    ratios = [by_name[m]["height"] / value for m, value in SALES.items()]
    middle = statistics.median(ratios)
    checks["bar heights are in proportion to the sales"] = middle > 0 and min(b["height"] for b in bars) >= 10 and \
        all(abs(r - middle) <= 0.03 * middle for r in ratios)
    bottoms = [bar["bottom"] for bar in bars]
    checks["the bars stand on one baseline"] = max(bottoms) - min(bottoms) <= 2
    others = {by_name[m]["colour"] for m in SALES if m != BEST}
    checks["the best month has its own colour"] = len(others) == 1 and by_name[BEST]["colour"] not in others
    names = [_label(seen["texts"], m, by_name[m], below=True) for m in SALES]
    numbers = [_label(seen["texts"], str(v), by_name[m], below=False) for m, v in SALES.items()]
    checks["each bar has its month under it"] = all(names)
    checks["each bar has its number above it"] = all(numbers)
    top = min(bar["top"] for bar in bars)
    title = next((t for t in seen["texts"] if "monthly sales" in t["text"].lower() and t["bottom"] <= top), None)
    checks["the title is above the chart"] = title is not None
    shown = [*bars, *(t for t in names + numbers + [title] if t)]
    inside = all(r["left"] >= 0 and r["top"] >= 0 and r["right"] <= WIDTH and r["bottom"] <= HEIGHT for r in shown)
    checks["the chart fits in 800 by 600 without scrolling"] = inside and seen["scrollWidth"] <= WIDTH and \
        seen["scrollHeight"] <= HEIGHT
    labels = [t for t in names + numbers + [title] if t]
    checks["no labels overlap"] = len(labels) >= 2 and not any(
        _overlap(a, b) > 1 for i, a in enumerate(labels) for b in labels[i + 1:])
    checks["no console errors or outside requests"] = not errors and not outside


def look(session, checks: dict, page_file: Path):
    page = session.open(page_file)
    page.set_viewport_size({"width": WIDTH, "height": HEIGHT})
    page.reload()
    page.wait_for_timeout(300)
    judge(page.evaluate(MEASURE), checks, session.errors, session.outside)


def grade(workspace: Path) -> dict:
    page_file = workspace / "chart.html"
    if not page_file.is_file():
        return graderlib.report({"chart.html exists": False}, total=TOTAL)
    return graderlib.grade_in_browser(lambda session, checks: look(session, checks, page_file), TOTAL)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("workspace")
    parser.add_argument("--json", required=True)
    args = parser.parse_args()
    graderlib.write(grade(Path(args.workspace)), args.json)


if __name__ == "__main__":
    sys.exit(main())
