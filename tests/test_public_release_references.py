"""Keep public installers, updater, and user-facing links on public main."""

import hashlib
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPO = "palindrome-rl/AGENT8088"
# SHA-256 digests of retired pre-release repository slugs. Stored as digests so
# the guard does not itself publish the names it keeps out of public files.
RETIRED_REPO_DIGESTS = {
    "13991250c7c764127c4b0c71407267528ad03a4199ec079b5336695a037609a1",
    "3c4f1d1e1f0f4383b07c231787be22c9fd915763dfa8d483ad4240be20d9f3b7",
}


def _retired_repo_refs(source: str) -> set[str]:
    slugs = set(re.findall(r"(?=([\w-]+/[\w-]+))", source))
    return {s for s in slugs if hashlib.sha256(s.encode()).hexdigest() in RETIRED_REPO_DIGESTS}


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_windows_installer_defaults_to_public_main():
    source = _read("install.ps1")
    assert 'else { "main" }' in source
    assert f'$RepoSlug = "{REPO}"' in source
    assert not _retired_repo_refs(source)


def test_unix_installer_defaults_to_public_main():
    source = _read("install.sh")
    assert f'REPO_URL="https://github.com/{REPO}.git"' in source
    assert 'REPO_BRANCH="${AGENT8088_BRANCH:-main}"' in source
    assert f"https://raw.githubusercontent.com/{REPO}/$BRANCH/install.sh" in source
    assert not _retired_repo_refs(source)


def test_installed_cli_updates_from_public_main():
    assert 'UPDATE_BRANCH = "main"' in _read("src/agent8088/cli.py")


def test_readme_installs_and_badges_public_main():
    readme = _read("README.md")
    quick_start = readme.split("## Quick start", 1)[1].split("## How Agent8088", 1)[0]
    assert "Install Agent8088" in quick_start
    assert f"{REPO}/main/install.sh" in quick_start
    assert f"{REPO}/main/install.ps1" in quick_start
    assert f"{REPO}/tree/main" in readme
    assert "/staging/" not in quick_start


def test_published_references_do_not_use_internal_or_legacy_repositories():
    paths = (
        "README.md",
        "install.ps1",
        "install.sh",
        "docs/wiki/01-getting-started.md",
        "docs/wiki/14-contributing.md",
        "docs/wiki/README.md",
        "scripts/sync_wiki.py",
    )
    for path in paths:
        source = _read(path)
        assert not _retired_repo_refs(source), path
