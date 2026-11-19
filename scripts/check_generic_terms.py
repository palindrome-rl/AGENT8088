#!/usr/bin/env python3
"""Check that a branch stays generic: no repeat-counting wording, and no private words.

Scans the lines a branch adds (against --base) and its commit messages. Two lists:

* built in: wording that fixes how many times to repeat work, which is the user's call;
* private, optional: one word or phrase per line in the file named by the
  AGENT8088_GENERIC_TERMS_FILE environment variable. Keep that file outside the
  repository; this script never stores or prints its location.

Exit 0 when clean, 1 with one line per hit.

    python scripts/check_generic_terms.py --base origin/development
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

# Built from parts so this file does not match itself.
_BUILT_IN = [r"\b" + "rou" + r"nds\b", "iter" + "at"]
_SKIP = {"scripts/check_generic_terms.py", "tests/test_browser_qa_skill.py"}


def _git(repo: str, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True,
                          encoding="utf-8", errors="replace").stdout


def _private_terms() -> list[str]:
    path = os.environ.get("AGENT8088_GENERIC_TERMS_FILE", "").strip()
    if not path:
        return []
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        print("generic-terms: the private terms file could not be read", file=sys.stderr)
        sys.exit(2)
    return [re.escape(line.strip()) for line in lines if line.strip() and not line.startswith("#")]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default="origin/development")
    parser.add_argument("--repo", default=".")
    args = parser.parse_args(argv)
    pattern = re.compile("|".join(_BUILT_IN + _private_terms()), re.IGNORECASE)
    hits = []
    current = None
    for line in _git(args.repo, "diff", "--unified=0", f"{args.base}...HEAD").splitlines():
        if line.startswith("+++ "):
            current = line[6:] if line.startswith("+++ b/") else None
            continue
        if current in _SKIP or current is None or not line.startswith("+") or line.startswith("+++"):
            continue
        match = pattern.search(line)
        if match:
            hits.append(f"{current}: added line contains '{match.group(0)}'")
    for sha in _git(args.repo, "rev-list", f"{args.base}..HEAD").split():
        message = _git(args.repo, "log", "-1", "--format=%B", sha)
        match = pattern.search(message)
        if match:
            hits.append(f"commit message {sha[:7]}: contains '{match.group(0)}'")
    for hit in hits:
        print(hit)
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
