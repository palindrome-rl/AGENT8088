"""The advertised branch, the installer defaults and the update target must agree.

A release channel is only as good as its weakest reference. Changing the README
alone leaves both installers defaulting elsewhere; changing the installers alone
leaves `agent8088 --update` pulling a user off the channel they chose on their
first update. Each of these has to name the same branch, so this reads the real
files rather than a constant, and fails if one of them drifts.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _channel() -> str:
    """The branch this checkout installs, taken from the shell installer."""
    text = (ROOT / "install.sh").read_text(encoding="utf-8")
    match = re.search(r'REPO_BRANCH="\$\{AGENT8088_BRANCH:-([^}"]+)\}"', text)
    assert match, "install.sh no longer declares a default branch in the expected form"
    return match.group(1)


def test_powershell_installer_defaults_to_the_same_branch():
    text = (ROOT / "install.ps1").read_text(encoding="utf-8")
    match = re.search(r'\$Branch = \$\(if \(\$env:AGENT8088_BRANCH\).*?else \{ "([^"]+)" \}\)', text)
    assert match, "install.ps1 no longer declares a default branch in the expected form"
    assert match.group(1) == _channel(), (
        "install.ps1 installs %r while install.sh installs %r; a user's platform "
        "would decide which version they get" % (match.group(1), _channel()))


def test_the_cli_updates_from_the_branch_it_was_installed_from():
    text = (ROOT / "src" / "agent8088" / "cli.py").read_text(encoding="utf-8")
    match = re.search(r'^UPDATE_BRANCH = "([^"]+)"', text, re.MULTILINE)
    assert match, "cli.py no longer declares UPDATE_BRANCH"
    assert match.group(1) == _channel(), (
        "agent8088 --update pulls %r onto a checkout installed from %r, so the "
        "first update silently moves the user to another channel"
        % (match.group(1), _channel()))


def test_the_readme_advertises_the_branch_that_is_installed():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    channel = _channel()
    urls = re.findall(r"AGENT8088/([^/]+)/install\.(?:sh|ps1)", text)
    assert urls or "agent8088-installer.pages.dev/install.sh" in text, (
        "README no longer shows an install command")
    wrong = sorted({u for u in urls if u != channel})
    assert not wrong, ("README fetches the installer from %s but this branch installs "
                       "%r" % (wrong, channel))
    declared = re.findall(r"AGENT8088_BRANCH\s*=\s*[\"']?([A-Za-z0-9._/-]+)", text)
    wrong = sorted({d for d in declared if d != channel})
    assert not wrong, ("README sets AGENT8088_BRANCH to %s but this branch installs "
                       "%r" % (wrong, channel))


def test_getting_started_matches_the_readme():
    page = ROOT / "docs" / "wiki" / "01-getting-started.md"
    text = page.read_text(encoding="utf-8")
    channel = _channel()
    urls = re.findall(r"AGENT8088/([^/]+)/install\.(?:sh|ps1)", text)
    wrong = sorted({u for u in urls if u != channel})
    assert not wrong, ("%s fetches the installer from %s, not %r"
                       % (page.name, wrong, channel))
    # A clone for contributing is allowed to name another branch: it is not an
    # install instruction. Only AGENT8088_BRANCH decides what gets installed.
    declared = re.findall(r"AGENT8088_BRANCH\s*=\s*'?\"?([A-Za-z0-9._/-]+)", text)
    wrong = sorted({d for d in declared if d != channel})
    assert not wrong, ("%s sets AGENT8088_BRANCH to %s, not %r"
                       % (page.name, wrong, channel))


def test_installer_bootstrap_and_update_urls_stay_on_the_channel():
    channel = _channel()
    for relative in ("install.sh", "install.ps1"):
        text = (ROOT / relative).read_text(encoding="utf-8")
        # Every channel must fetch the installer from this repository, including
        # bootstrap/relaunch and retry paths; a branch variable alone is not enough.
        assert "agent8088-installer.pages.dev" not in text
        concrete = re.findall(
            r"raw\.githubusercontent\.com/palindrome-rl/"
            r"AGENT8088/([^/\s$]+)/install\.(?:sh|ps1)", text
        )
        wrong = sorted({branch for branch in concrete if branch != channel})
        assert not wrong, f"{relative} advertises installer branches {wrong}, not {channel!r}"


def test_every_branch_carries_the_production_channel():
    """development, staging-1.3 and AGENT8088-v1.3 share one tree. The installers
    default to production, other channels are chosen with AGENT8088_BRANCH, and
    `--update` follows the branch an install is on (tests/test_update_follows_installed_branch.py)."""
    assert _channel() == "AGENT8088-v1.3"
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "/tree/AGENT8088-v1.3" in readme
    wiki_home = (ROOT / "docs" / "wiki" / "README.md").read_text(encoding="utf-8")
    assert "Staging 1.2 documentation" not in wiki_home


def test_readme_and_agent_install_guide_fetch_the_selected_channel():
    channel = _channel()
    base = f"https://raw.githubusercontent.com/palindrome-rl/AGENT8088/{channel}/"
    for relative in ("README.md", "docs/install-for-agents.md"):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert base + "install.sh" in text, relative
        assert base + "install.ps1" in text, relative
        assert "agent8088-installer.pages.dev" not in text, relative
        assert "repository is private" not in text, relative


def test_agent_install_guide_does_not_require_github_authentication():
    text = (ROOT / "docs/install-for-agents.md").read_text(encoding="utf-8")
    assert re.search(r"public repository|repository is public", text)
    assert "gh api" not in text and "gh auth login" not in text
    assert "Invoke-RestMethod" in text
