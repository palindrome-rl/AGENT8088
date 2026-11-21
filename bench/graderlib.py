"""Helpers the task graders share. Imported by path from bench/tasks/<task>/.

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    import graderlib
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def installed_chromes() -> list:
    """Chromium executables in the Playwright browsers folder, newest first.

    Used when the installed Playwright expects a browser build other than the one
    downloaded; any recent Chromium renders these pages the same way."""
    root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if not root:
        return []
    names = ("chrome.exe", "chrome", "chrome-headless-shell.exe", "chrome-headless-shell", "Chromium")
    found = [path for path in Path(root).glob("chromium*/**/*") if path.name in names and path.is_file()]
    return sorted(found, key=str, reverse=True)


def launch_chromium(driver):
    """Start Chromium, trying the other installed builds when the expected one is missing."""
    failures = []
    for executable in [None, *installed_chromes()]:
        try:
            return driver.chromium.launch(executable_path=str(executable) if executable else None)
        except Exception as exc:  # noqa: BLE001 - every launch failure means: try the next build
            failures.append(exc)
    raise failures[0]


def run_checker(check: Path, args: list, timeout: int = 1800) -> dict:
    """Run a task's original checker, which prints one JSON report, and return that report.

    A checker that crashes or prints no JSON gives {"error": ...}, so the run counts
    as ungraded rather than failed."""
    try:
        done = subprocess.run([sys.executable, str(check), *map(str, args)], capture_output=True, text=True,
                              timeout=timeout, encoding="utf-8", errors="replace", check=False)
    except subprocess.TimeoutExpired:
        return {"error": f"checker timed out after {timeout}s"}
    text = done.stdout.strip()
    start = text.find("{")
    try:
        return json.loads(text[start:]) if start >= 0 else {"error": f"checker printed no report: {done.stderr[-300:]}"}
    except ValueError:
        return {"error": f"checker report is not JSON: {done.stderr[-300:]}"}


def write(report: dict, path: str) -> None:
    Path(path).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
