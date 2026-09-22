"""Publishing this branch's wiki pages must not delete anyone else's.

GitHub gives a repository one wiki, not one per branch. The publish path used to
delete every markdown page before regenerating, so syncing from here would erase
pages belonging to another channel -- silently, and only visible to whoever went
looking for them afterwards.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "sync_wiki.py"


def test_the_publish_step_only_removes_pages_it_regenerates():
    text = SCRIPT.read_text(encoding="utf-8")
    # The unlink must be guarded by membership of the generated set. An
    # unconditional delete over clone.glob("*.md") is the defect this guards.
    unlink = re.search(r"for existing in clone\.glob\(\"\*\.md\"\):\s*\n(.*?)\n\s*for name",
                       text, re.DOTALL)
    assert unlink, "the publish loop no longer has the expected shape"
    body = unlink.group(1)
    assert "if existing.name in owned" in body, (
        "sync_wiki deletes every wiki page before regenerating; pages published "
        "from another branch would be destroyed on the next sync")


def test_pages_are_sourced_from_the_branch_that_generated_them():
    text = SCRIPT.read_text(encoding="utf-8")
    branch = re.search(r'^SOURCE_BRANCH = "([^"]+)"', text, re.MULTILINE)
    assert branch, "sync_wiki no longer declares which branch it publishes from"

    installed = re.search(r'REPO_BRANCH="\$\{AGENT8088_BRANCH:-([^}"]+)\}"',
                          (ROOT / "install.sh").read_text(encoding="utf-8"))
    assert installed, "install.sh no longer declares a default branch"
    assert branch.group(1) == installed.group(1), (
        "the wiki links readers to %r while this branch installs %r, so a reader "
        "following a source link lands on different text"
        % (branch.group(1), installed.group(1)))

    assert 'REPO = "palindrome-rl/AGENT8088"' in text, (
        "sync_wiki publishes to, and links at, a repository that is not this one")
