"""install.ps1 must set $env:PLAYWRIGHT_BROWSERS_PATH to a directory inside
$Agent8088Home before downloading Chromium - same reasoning as
test_installer_playwright_browsers_path.py's POSIX counterpart. A static
structural check on the source, matching the convention used elsewhere in
this suite for install.ps1/install.sh wiring checks that don't need to
execute the surrounding (large, dependency-heavy) installer function.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_playwright_browsers_path_set_before_chromium_install():
    source = (ROOT / "install.ps1").read_text(encoding="utf-8")
    match = re.search(
        r'(?ms)(\$env:PLAYWRIGHT_BROWSERS_PATH\s*=.*\n)?'
        r'\s*\$chromiumResult = Invoke-WithTimeout -FilePath \$py `\n'
        r'\s*-Arguments @\("-m", "playwright", "install", "chromium"\)',
        source,
    )
    assert match, "chromium install call not found in install.ps1"
    assert match.group(1), (
        "$env:PLAYWRIGHT_BROWSERS_PATH must be set immediately before the "
        "chromium install call, so the download lands inside $Agent8088Home"
    )
    assert '"$Agent8088Home\\playwright-browsers"' in match.group(1)
