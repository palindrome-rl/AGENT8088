"""Keep public installers, updater, and user-facing links on public main."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPO = "palindrome-rl/AGENT8088"
INTERNAL_REPO = "RT-Internal-DS/Agent8088-Features-added"
LEGACY_REPO = "tayyabimam1/Agent8088-Features-added"


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_windows_installer_defaults_to_public_main():
    source = _read("install.ps1")
    assert 'else { "main" }' in source
    assert f'$RepoSlug = "{REPO}"' in source
    assert INTERNAL_REPO not in source
    assert LEGACY_REPO not in source


def test_unix_installer_defaults_to_public_main():
    source = _read("install.sh")
    assert f'REPO_URL="https://github.com/{REPO}.git"' in source
    assert 'REPO_BRANCH="${AGENT8088_BRANCH:-main}"' in source
    assert f"https://raw.githubusercontent.com/{REPO}/$BRANCH/install.sh" in source
    assert INTERNAL_REPO not in source
    assert LEGACY_REPO not in source


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
        assert INTERNAL_REPO not in source, path
        assert LEGACY_REPO not in source, path
