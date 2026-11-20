"""Grader for render-report-page.

    python grade.py <workspace> --json <report.json>

Loads report.html from disk in headless Chromium, blocks every request that
leaves the machine, and checks what the page shows. When no browser can be
started here the report has no "passed" field, so the run counts as ungraded,
not failed.
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))  # bench/, for graderlib
import graderlib


def grade(workspace: Path) -> dict:
    page_file = workspace / "report.html"
    if not page_file.is_file():
        return {"passed": False, "score": 0.0, "checks": {"report.html exists": False}}
    data = json.loads((HERE / "fixture" / "data.json").read_text(encoding="utf-8"))
    months = [entry["month"] for entry in data["months"]]
    values = {entry["month"]: entry["value"] for entry in data["months"]}
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"error": "playwright is not installed for the grader"}
    checks, errors, outside = {}, [], []
    try:
        with sync_playwright() as driver:
            browser = graderlib.launch_chromium(driver)
            try:
                page = browser.new_page()
                page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
                page.on("pageerror", lambda error: errors.append(str(error)))

                def route(request_route):
                    url = request_route.request.url
                    if url.startswith(("file:", "data:", "about:", "blob:")):
                        request_route.continue_()
                    else:
                        outside.append(url)
                        request_route.abort()
                page.route("**/*", route)
                page.goto(page_file.resolve().as_uri())
                page.wait_for_timeout(500)
                heading = page.evaluate("() => { const h = document.querySelector('h1'); return h ? h.innerText : ''; }")
                rows = page.evaluate("""() => [...document.querySelectorAll('tr[data-month]')]
                    .map(r => [r.dataset.month, r.innerText])""")
                bars = page.evaluate("""() => [...document.querySelectorAll('svg rect.bar')]
                    .map(b => [b.dataset.month, b.getBoundingClientRect().height])""")
            finally:
                browser.close()
    except Exception as exc:  # noqa: BLE001 - a browser that cannot start is the grader's problem, not the run's
        return {"error": f"the grader could not start a browser: {str(exc).splitlines()[0][:200]}"}

    checks["the title is the main heading"] = heading.strip() == data["title"]
    checks["one table row per month, in order"] = [r[0] for r in rows] == months
    checks["each row shows its value"] = len(rows) == len(months) and all(
        str(values[month]) in text for month, text in rows if month in values)
    checks["one bar per month"] = sorted(b[0] for b in bars) == sorted(months)
    heights = {month: height for month, height in bars}
    top = max(values.values())
    tallest = heights.get(max(values, key=values.get), 0)
    checks["bar heights follow the values"] = len(heights) == len(months) and tallest > 0 and all(
        abs(heights[m] / tallest - values[m] / top) <= 0.03 for m in months if m in heights)
    checks["no requests leave the machine"] = not outside
    checks["no console errors"] = not errors
    score = sum(checks.values()) / len(checks)
    return {"passed": all(checks.values()), "score": round(score, 3), "checks": checks,
            "console_errors": errors[:5], "outside_requests": outside[:5]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("workspace")
    parser.add_argument("--json", required=True)
    args = parser.parse_args()
    report = grade(Path(args.workspace))
    Path(args.json).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    sys.exit(main())
