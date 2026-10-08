"""Optional install stages the installer skipped, from $AGENT8088_HOME/install-state.json.

install.sh / install.ps1 rewrite that file at the end of every run:

    {"version": 1, "installed_at": "2026-10-06T12:00:00Z",
     "skipped": [{"stage": "Chromium browser", "reason": "failed (exit 1)",
                  "fix": "python -m playwright install chromium"}]}

report() turns it into capabilities.INSTALL (degraded, one entry for all the
skipped stages) so the banner, /status and /doctor show it via the registry.

Two things keep it honest:
  * a stage that has a live subsystem entry in the registry (the browser,
    search, OCR, ...) is left to that entry — it knows the current state,
    the file only knows what happened at install time;
  * a stage whose dependency is now present (located with find_spec / which,
    never imported) is dropped: it was fixed since.
What remains is labelled "at install time", since it is not re-checked.

Cost: one small file read (and a few find_spec calls) per report().
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path

from agent8088 import capabilities

FILE_NAME = "install-state.json"

def _module(name):
    def check():
        try:
            return importlib.util.find_spec(name) is not None
        except (ImportError, ValueError):
            return False
    return check


def _any_command(*names):
    return lambda: any(shutil.which(n) for n in names)


def _chromium_present():
    root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or str(home() / "playwright-browsers")
    try:
        return any(p.name.startswith("chromium") for p in Path(root).iterdir())
    except OSError:
        return False


# Installer stage label (prefix match, lowercased) -> (registry capability that
# reports the live state, or "", and a check that says the stage is satisfied
# now, or None). Labels are the ones install.sh/install.ps1 pass to
# record_skip/warn_stage (Register-SkippedStage/Write-StageWarning).
_STAGES = (
    ("chromium", capabilities.BROWSER, _chromium_present),
    ("playwright", capabilities.BROWSER, _module("playwright")),
    ("keyless web search", capabilities.SEARCH, _module("ddgs")),
    ("ocr engine", capabilities.OCR, _module("rapidocr")),
    ("ocr models", capabilities.OCR, None),
    ("libreoffice", capabilities.DOCUMENTS, _any_command("soffice", "libreoffice")),
    ("mem0", capabilities.MEMORY, _module("mem0")),
    ("embedding model", capabilities.MEMORY_EMBED, None),
    ("gateway adapter", capabilities.GATEWAY, _module("slack_bolt")),
    ("native sandbox", capabilities.SANDBOX, None),
    ("repository context", "", _module("gitingest")),
)


def home() -> Path:
    if os.environ.get("AGENT8088_HOME"):
        return Path(os.environ["AGENT8088_HOME"]).expanduser()
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "agent8088"
    return Path.home() / ".agent8088"


def path() -> Path:
    return home() / FILE_NAME


def load(file: Path | None = None) -> dict | None:
    """The parsed file, or None when absent/unreadable/not ours."""
    file = file or path()
    try:
        data = json.loads(file.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("version") != 1:
        return None
    skipped = data.get("skipped")
    if not isinstance(skipped, list):
        return None
    data["skipped"] = [
        {"stage": str(item.get("stage") or ""), "reason": str(item.get("reason") or ""),
         "fix": str(item.get("fix") or "")}
        for item in skipped if isinstance(item, dict) and item.get("stage")]
    return data


def _stage_rule(stage: str):
    lowered = stage.lower()
    for prefix, capability, check in _STAGES:
        if lowered.startswith(prefix):
            return capability, check
    return "", None


def outstanding(data: dict) -> list[dict]:
    """Skipped stages that neither a live registry entry nor a present
    dependency accounts for."""
    remaining = []
    for item in data.get("skipped", []):
        # Declining an explicitly optional, slow component is a user choice,
        # not a partial installation. Keep it in install-state.json for
        # diagnostics, but don't warn on every CLI startup about it.
        if item["reason"].lower().startswith("not selected (optional"):
            continue
        capability, check = _stage_rule(item["stage"])
        if capability and capabilities.get(capability) is not None:
            continue  # the live subsystem entry is authoritative
        try:
            if check is not None and check():
                continue  # fixed since the install
        except Exception:  # noqa: BLE001 — a probe must never break startup
            pass
        remaining.append(item)
    return remaining


def report(file: Path | None = None) -> list[dict]:
    """Report capabilities.INSTALL from the file; return the stages reported.

    No file (a source checkout, an install predating it) clears the entry;
    so does a file listing nothing still outstanding."""
    data = load(file)
    items = outstanding(data) if data else []
    if not items:
        capabilities.clear(capabilities.INSTALL)
        return []
    stages = [item["stage"] for item in items]
    when = f" ({data['installed_at']})" if data.get("installed_at") else ""
    fixes = [item["fix"] for item in items if item["fix"]]
    capabilities.report(
        capabilities.INSTALL,
        active="partial",
        preferred="complete",
        state=capabilities.DEGRADED,
        reason=f"skipped at install time{when}: " + "; ".join(
            f"{item['stage']} ({item['reason']})" if item["reason"] else item["stage"]
            for item in items),
        impact="not installed at install time: " + ", ".join(stages),
        fix=" ; ".join(fixes) if fixes else "re-run the installer",
    )
    return items
