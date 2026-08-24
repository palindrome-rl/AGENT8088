"""install.sh must point PLAYWRIGHT_BROWSERS_PATH at $AGENT8088_HOME before
downloading Chromium, so the browser lands somewhere `agent8088 --uninstall`
already cleans up instead of the OS-shared ms-playwright cache (which other
Playwright-using projects on the same machine may depend on).

The Chromium install step lives inside install_deps(), a large function with
many dependencies (UV_CMD, run_with_timeout, warn_stage, ...) that aren't
safe to stub and execute in isolation. Following the same static-wiring-check
convention test_installer_sudo_prompt_foreground.py already uses for the
`sudo -v` foreground requirement, this is a structural check on the source
rather than an execution test.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_playwright_browsers_path_exported_before_chromium_install():
    source = (ROOT / "install.sh").read_text(encoding="utf-8")
    match = re.search(
        r'(?ms)^\s*(export PLAYWRIGHT_BROWSERS_PATH=.*\n)?'
        r'\s*run_with_timeout "\$T_CHROMIUM" "\$_py" -m playwright install chromium',
        source,
    )
    assert match, "chromium install call not found in install.sh"
    assert match.group(1), (
        "PLAYWRIGHT_BROWSERS_PATH must be exported immediately before the "
        "chromium install call, so the download lands inside $AGENT8088_HOME"
    )
    assert '"$AGENT8088_HOME/playwright-browsers"' in match.group(1)


def test_playwright_browsers_path_matches_engine_default():
    """The value install.sh exports must be the exact directory name
    engine.py's _exec_browser sets as its own default (Task 1) - a drift
    between the two would make a fresh install download Chromium to one
    path while the runtime looks in another."""
    install_source = (ROOT / "install.sh").read_text(encoding="utf-8")
    engine_source = (ROOT / "src" / "agent8088" / "engine.py").read_text(encoding="utf-8")

    assert '$AGENT8088_HOME/playwright-browsers' in install_source
    assert '_agent_data_dir() / "playwright-browsers"' in engine_source
