#!/usr/bin/env python3
"""Mirror docs/wiki/ into the GitHub Wiki.

`docs/wiki/` is the source of truth: it is versioned with the code and reviewed
in PRs. The GitHub Wiki tab is a *separate* git repository
(`<repo>.wiki.git`) with different conventions, so publishing is a conversion,
not a copy:

  - the landing page must be `Home.md`
  - pages are addressed by name with no `.md`, so every internal link is
    rewritten (`[Tools](04-tools.md)` -> `[Tools](Tools)`)
  - numeric filename prefixes look wrong in the wiki's page list, so they are
    dropped in favour of title-cased names
  - a `_Sidebar.md` is generated for navigation

Usage:
    python scripts/sync_wiki.py --dry-run     # show what would change
    python scripts/sync_wiki.py               # convert, commit and push

Requires push access to the wiki repo, and the wiki must already be
initialised — GitHub does not create `<repo>.wiki.git` until the first page is
saved through the web UI.
"""
import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = "palindrome-rl/AGENT8088"
SOURCE_DIR = Path(__file__).resolve().parent.parent / "docs" / "wiki"

# source filename -> wiki page name (no .md). Order defines the sidebar.
PAGES = [
    ("README.md", "Home"),
    ("01-getting-started.md", "Getting-Started"),
    ("02-configuration.md", "Configuration"),
    ("03-permissions-and-security.md", "Permissions-and-Security"),
    ("04-tools.md", "Tools"),
    ("05-model-providers.md", "Model-Providers"),
    ("06-sandboxing.md", "Sandboxing"),
    ("07-mcp.md", "MCP"),
    ("08-messaging-gateway.md", "Messaging-Gateway"),
    ("09-skills-and-subagents.md", "Skills-and-Subagents"),
    ("10-cli-reference.md", "CLI-Reference"),
    ("11-architecture.md", "Architecture"),
    ("12-testing-and-verification.md", "Testing-and-Verification"),
    ("13-troubleshooting.md", "Troubleshooting"),
    ("14-contributing.md", "Contributing"),
    ("15-faq.md", "FAQ"),
    ("16-memory.md", "Memory"),
    ("17-docker.md", "Docker"),
]

# Sidebar grouping: guides first, then a Reference section split by kind, then
# development docs. Published page names stay decoupled from filenames via PAGES
# above, so regrouping here never changes a wiki URL.
SIDEBAR_SECTIONS = [
    ("Start here", ["Home", "Getting-Started"]),
    ("Guides", [
        "Permissions-and-Security",
        "Sandboxing",
        "Model-Providers",
        "MCP",
        "Messaging-Gateway",
        "Skills-and-Subagents",
        "Docker",
    ]),
    ("Reference", [
        "CLI-Reference",
        "Configuration",
        "Tools",
        "Memory",
        "FAQ",
        "Troubleshooting",
    ]),
    ("Development", ["Architecture", "Testing-and-Verification", "Contributing"]),
]

BANNER = (
    "<!-- DO NOT EDIT HERE. Generated from docs/wiki/ in the main repository by\n"
    "     scripts/sync_wiki.py. Edits made in the wiki UI will be overwritten on\n"
    "     the next sync — change the source file and open a PR instead. -->\n\n"
)

SOURCE_NOTE = (
    "\n\n---\n\n*Source of truth: [`docs/wiki/`](https://github.com/"
    f"{REPO}/tree/main/docs/wiki) in the main repository. "
    "Edits here are overwritten by the next sync.*\n"
)


def convert(text: str, link_map: dict) -> str:
    """Rewrite internal .md links to wiki page names, preserving anchors."""
    def replace(match):
        target, anchor = match.group(1), match.group(2) or ""
        page = link_map.get(target)
        return f"]({page}{anchor})" if page else match.group(0)

    return re.sub(r"\]\(([0-9A-Za-z._-]+\.md)(#[^)]*)?\)", replace, text)


def build_sidebar() -> str:
    known = {page for _, page in PAGES}
    grouped = {page for _, pages in SIDEBAR_SECTIONS for page in pages}
    # A page added to PAGES but not to a section would vanish from the sidebar,
    # so fail loudly rather than publishing navigation that silently omits it.
    missing = known - grouped
    if missing:
        raise SystemExit(
            f"sync_wiki: page(s) not assigned to a sidebar section: {sorted(missing)}"
        )
    unknown = grouped - known
    if unknown:
        raise SystemExit(f"sync_wiki: sidebar references unknown page(s): {sorted(unknown)}")

    lines = ["### Agent8088 Wiki", ""]
    for section, pages in SIDEBAR_SECTIONS:
        lines.append(f"**{section}**")
        lines.append("")
        for page in pages:
            label = "Home" if page == "Home" else page.replace("-", " ")
            lines.append(f"- [{label}]({page})")
        lines.append("")
    lines.append(f"[Repository](https://github.com/{REPO})")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="convert and report, but do not clone or push")
    args = parser.parse_args()

    missing = [src for src, _ in PAGES if not (SOURCE_DIR / src).is_file()]
    if missing:
        print(f"error: missing source pages: {', '.join(missing)}", file=sys.stderr)
        return 1

    link_map = {src: page for src, page in PAGES}
    converted = {}
    for src, page in PAGES:
        text = (SOURCE_DIR / src).read_text(encoding="utf-8")
        body = convert(text, link_map)
        converted[f"{page}.md"] = BANNER + body.rstrip("\n") + SOURCE_NOTE

    converted["_Sidebar.md"] = BANNER + build_sidebar()

    # Any internal .md link that survived conversion is a broken wiki link.
    stale = set()
    for name, body in converted.items():
        for match in re.finditer(r"\]\(([0-9A-Za-z._-]+\.md)(#[^)]*)?\)", body):
            stale.add(f"{name} -> {match.group(1)}")
    if stale:
        print("error: unconverted internal links:", file=sys.stderr)
        for item in sorted(stale):
            print(f"  {item}", file=sys.stderr)
        return 1

    print(f"Converted {len(PAGES)} pages + _Sidebar.md, no broken links.")
    if args.dry_run:
        for name in converted:
            print(f"  would write {name}")
        return 0

    workdir = Path(tempfile.mkdtemp(prefix="agent8088-wiki-"))
    try:
        clone = workdir / "wiki"
        result = subprocess.run(
            ["git", "clone", "--quiet", f"https://github.com/{REPO}.wiki.git", str(clone)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print("error: could not clone the wiki repo. Has the wiki been\n"
                  "initialised? GitHub only creates <repo>.wiki.git after the\n"
                  f"first page is saved in the UI.\n{result.stderr}", file=sys.stderr)
            return 1

        for existing in clone.glob("*.md"):
            existing.unlink()
        for name, body in converted.items():
            (clone / name).write_text(body, encoding="utf-8")

        subprocess.run(["git", "add", "-A"], cwd=clone, check=True)
        status = subprocess.run(["git", "status", "--porcelain"], cwd=clone,
                                capture_output=True, text=True).stdout.strip()
        if not status:
            print("Wiki already up to date — nothing to push.")
            return 0

        subprocess.run(
            ["git", "commit", "--quiet", "-m",
             "docs: sync wiki from docs/wiki/ via scripts/sync_wiki.py"],
            cwd=clone, check=True,
        )
        push = subprocess.run(["git", "push", "--quiet"], cwd=clone,
                              capture_output=True, text=True)
        if push.returncode != 0:
            print(f"error: push failed\n{push.stderr}", file=sys.stderr)
            return 1
        print(f"Pushed {len(converted)} pages to https://github.com/{REPO}/wiki")
        return 0
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
