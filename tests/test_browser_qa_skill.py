"""The browser-qa skill, the published tool and skill counts, and the generic-terms check."""
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "src" / "agent8088" / "skills_installed"


# --- browser-qa -------------------------------------------------------------------------------------------

def test_browser_qa_is_a_progressive_skill_that_needs_the_render_tool(engine):
    skill = engine.SKILL_PACKAGES["browser-qa"]
    assert skill["progressive"] is True
    assert skill["requires_tools"] == {"screenshot_page"}
    text = engine.read_skill_resource("browser-qa", "SKILL.md")
    for tool in ("check_app", "screenshot_page", "view_image", "edit_file", "execute_shell"):
        assert tool in text, tool


def test_browser_qa_is_listed_for_the_model(engine):
    assert "browser-qa" in engine.current_system_prompt(native_tools=True)


def test_browser_qa_activates_for_a_visual_check(engine):
    names = engine.select_task_skills([
        {"role": "user", "content": "Build a signup page and check that it looks right on a phone."}])
    assert "browser-qa" in names


def test_browser_qa_stays_hidden_without_the_render_tool(engine):
    names = engine.select_task_skills(
        [{"role": "user", "content": "Build a signup page and check that it looks right on a phone."}],
        available_tools=set(engine.TOOL_NAMES) - {"screenshot_page"})
    assert "browser-qa" not in names


def test_browser_qa_examples_are_varied_and_it_sets_no_repeat_count():
    text = (SKILLS / "browser-qa" / "SKILL.md").read_text(encoding="utf-8").lower()
    kinds = ["form", "chart", "layout", "drawing", "canvas"]
    assert sum(kind in text for kind in kinds) >= 4, "examples cover several kinds of output"
    assert not re.search(r"\b(?:rounds?|iterations?|iterate|passes)\b.*\b\d+\b|\b\d+\s+(?:rounds?|iterations?|times)\b",
                         text), "how often to repeat is the user's call, not the skill's"


# --- published counts -------------------------------------------------------------------------------------

def _core_tool_count():
    lines = (ROOT / "src" / "agent8088" / "tools.txt").read_text(encoding="utf-8").splitlines()
    return sum(1 for line in lines if line.strip() and not line.startswith("#") and "|" in line)


def _skill_count():
    return sum(1 for p in SKILLS.iterdir() if p.is_dir() and not p.name.startswith("_") and (p / "SKILL.md").exists())


def test_the_readme_badges_match_the_tool_and_skill_counts():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert f"{_core_tool_count()}%20built--in%20tools" in readme
    assert f"{_skill_count()}%20skill%20packages" in readme


def test_the_wiki_skill_count_matches():
    count = _skill_count()
    assert f"### The {count} bundled skills" in (ROOT / "docs/wiki/09-skills-and-subagents.md").read_text(encoding="utf-8")
    assert f"**{count}** packages" in (ROOT / "docs/wiki/README.md").read_text(encoding="utf-8")


# --- generic-terms check ----------------------------------------------------------------------------------

SCRIPT = ROOT / "scripts" / "check_generic_terms.py"


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.invalid")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "a.txt").write_text("start\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "base")
    _git(tmp_path, "branch", "base")
    return tmp_path


def _check(repo, *extra, env=None):
    import os
    run_env = dict(os.environ)
    run_env.pop("AGENT8088_GENERIC_TERMS_FILE", None)
    run_env.update(env or {})
    return subprocess.run([sys.executable, str(SCRIPT), "--base", "base", "--repo", str(repo), *extra],
                          capture_output=True, text=True, env=run_env)


def test_a_clean_change_passes(repo):
    (repo / "a.txt").write_text("start\nrender the lighthouse page\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "Add a page")
    assert _check(repo).returncode == 0


def test_repeat_counting_words_are_flagged_in_added_lines_and_messages(repo):
    word = "iter" + "ations"
    (repo / "a.txt").write_text(f"start\nrun three {word}\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "plain message")
    result = _check(repo)
    assert result.returncode == 1 and ("iter" + "at") in result.stdout and "a.txt" in result.stdout
    (repo / "a.txt").write_text("start\nclean\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "Two more " + "rou" + "nds")
    result = _check(repo)
    assert result.returncode == 1 and "commit message" in result.stdout


def test_extra_words_come_from_a_file_outside_the_repo(repo, tmp_path_factory):
    words = tmp_path_factory.mktemp("private") / "terms.txt"
    words.write_text("zebra\n", encoding="utf-8")
    (repo / "a.txt").write_text("start\na zebra appears\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "Add text")
    assert _check(repo).returncode == 0, "no extra words without the file"
    result = _check(repo, env={"AGENT8088_GENERIC_TERMS_FILE": str(words)})
    assert result.returncode == 1 and "zebra" in result.stdout


def test_the_script_holds_no_word_list_of_its_own_beyond_the_generic_terms():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "AGENT8088_GENERIC_TERMS_FILE" in text
