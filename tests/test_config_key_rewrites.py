"""The shipped config.txt and the code that rewrites it must agree.

config.txt documents several commented `search_base_url=<example>` endpoints so a
user can see what a valid value looks like. Three separate rewriters — install.sh,
install.ps1 and cli.py's setup wizard — used to match `^#?\\s*search_base_url=`,
which hit every one of those comment lines: entering a search URL during install
rewrote all four into duplicate ACTIVE keys and destroyed the documentation.

These tests run the real patterns, lifted out of the real files, against the real
config, so the config's comments and the rewriters cannot drift apart again.
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "src" / "agent8088" / "config.txt"
NEW_URL = "http://127.0.0.1:9999/search?q="


def _active_lines(text, key="search_base_url"):
    """Uncommented `key=` lines — the ones the engine actually reads."""
    return re.findall(rf'^{key}=.*$', text, re.MULTILINE)


def _commented_examples(text, key="search_base_url"):
    return re.findall(rf'^#\s*{key}=.*$', text, re.MULTILINE)


def test_shipped_config_documents_examples_and_activates_none():
    text = CONFIG.read_text(encoding="utf-8")
    assert _active_lines(text) == [], "no search endpoint may ship active"
    assert len(_commented_examples(text)) >= 2, (
        "the examples users copy from should not disappear silently")


def _sed_expressions():
    """The search_base_url sed scripts install.sh actually runs."""
    text = (ROOT / "install.sh").read_text(encoding="utf-8")
    found = re.findall(r'sed -i\.bak (["\'])(.+?search_base_url.+?)\1', text)
    assert found, "install.sh no longer rewrites search_base_url — update this test"
    return [expr for _, expr in found]


@pytest.mark.skipif(shutil.which("sed") is None, reason="sed unavailable")
def test_install_sh_rewrite_yields_exactly_one_active_key(tmp_path):
    """A user typing a search URL must get ONE active key, examples intact."""
    config = tmp_path / "config.txt"
    config.write_text(CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
    before = len(_commented_examples(config.read_text(encoding="utf-8")))

    replace = [e for e in _sed_expressions() if e.startswith("s|")]
    assert replace, "no replace expression found in install.sh"
    for expr in replace:
        expr = expr.replace("$new_search", NEW_URL).replace("$config", str(config))
        subprocess.run(["sed", "-i.bak", expr, str(config)], check=True)

    text = config.read_text(encoding="utf-8")
    # install.sh appends the key when sed found nothing to replace; emulate that
    # step so the assertion covers the branch as shipped.
    if not _active_lines(text):
        text += f"search_base_url={NEW_URL}\n"
    assert _active_lines(text) == [f"search_base_url={NEW_URL}"]
    assert len(_commented_examples(text)) == before, "examples were rewritten"


@pytest.mark.skipif(shutil.which("sed") is None, reason="sed unavailable")
def test_install_sh_disable_keeps_the_examples(tmp_path):
    """`none` removes the active key only — the documentation stays."""
    config = tmp_path / "config.txt"
    seeded = CONFIG.read_text(encoding="utf-8") + f"search_base_url={NEW_URL}\n"
    config.write_text(seeded, encoding="utf-8")
    before = len(_commented_examples(seeded))

    delete = [e for e in _sed_expressions() if e.endswith("/d")]
    assert delete, "no delete expression found in install.sh"
    for expr in delete:
        subprocess.run(["sed", "-i.bak", expr, str(config)], check=True)

    text = config.read_text(encoding="utf-8")
    assert _active_lines(text) == []
    assert len(_commented_examples(text)) == before, "examples were deleted"


def test_powershell_installer_pattern_matches_only_active_keys():
    """install.ps1 cannot be executed here, so its regex is checked directly."""
    text = (ROOT / "install.ps1").read_text(encoding="utf-8")
    patterns = re.findall(r"-replace '\(\?m\)(\^[^']*search_base_url[^']*)'", text)
    assert patterns, "install.ps1 no longer rewrites search_base_url"
    config = CONFIG.read_text(encoding="utf-8")
    for pattern in patterns:
        # \r? and PowerShell's (?m) map onto Python's MULTILINE directly.
        hits = re.findall(pattern.replace(r"\r", ""), config, re.MULTILINE)
        assert hits == [], f"{pattern!r} matches shipped comments: {hits}"


def test_cli_setup_disable_pattern_matches_only_active_keys():
    text = (ROOT / "src" / "agent8088" / "cli.py").read_text(encoding="utf-8")
    patterns = re.findall(r"_re\.sub\(r'(\^[^']*search_base_url[^']*)'", text)
    assert patterns, "cli.py no longer rewrites search_base_url"
    config = CONFIG.read_text(encoding="utf-8")
    for pattern in patterns:
        hits = re.findall(pattern, config, re.MULTILINE)
        assert hits == [], f"{pattern!r} matches shipped comments: {hits}"
