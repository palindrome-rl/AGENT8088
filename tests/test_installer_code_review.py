"""Installer wiring for the optional OpenCodeReview engine."""
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
needs_windows = pytest.mark.skipif(sys.platform != "win32", reason="install.ps1 is Windows-only")


def _function(source: str, name: str, shell: str) -> str:
    marker = "function " + re.escape(name) if shell == "ps" else re.escape(name) + r"\(\)"
    match = re.search(rf"(?ms)^{marker} \{{.*?^\}}$", source)
    assert match, f"function not found: {name}"
    return match.group(0)


def test_windows_install_runs_review_after_node_and_before_config():
    source = (ROOT / "install.ps1").read_text(encoding="utf-8")
    main = source[source.rfind("try {"):]
    assert main.index("Install-Node-Bridge") < main.index("Install-CodeReview") < main.index("Drop-Config")
    review = _function(source, "Install-CodeReview", "ps")
    assert "npm.cmd" in review
    assert "$script:ConfigFile" not in review


def test_posix_install_runs_review_after_node_and_before_config():
    source = (ROOT / "install.sh").read_text(encoding="utf-8")
    main = _function(source, "main", "sh")
    assert main.index("install_node_bridge") < main.index("install_code_review") < main.index("drop_config")
    review = _function(source, "install_code_review", "sh")
    assert "$CONFIG_FILE" not in review


def test_posix_config_update_is_idempotent():
    git_bash = Path(r"C:\Program Files\Git\bin\bash.exe")
    bash = str(git_bash) if git_bash.exists() else shutil.which("bash")
    if not bash:
        pytest.skip("bash is unavailable")
    source = (ROOT / "install.sh").read_text(encoding="utf-8")
    function = _function(source, "set_configured_code_review", "sh")
    command = (function + '\nconfig=$(mktemp)\n'
               'printf "open_code_review_enabled=0\\nfoo=bar\\n" > "$config"\n'
               'set_configured_code_review "$config" "/opt/ocr/bin/opencodereview"\n'
               'set_configured_code_review "$config" "/opt/ocr/bin/opencodereview"\n'
               'cat "$config"\nrm -f "$config"\n')
    result = subprocess.run([bash, "-c", command], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    text = result.stdout
    assert text.count("open_code_review_enabled=") == 1
    assert text.count("open_code_review_executable=") == 1
    assert "open_code_review_enabled=1" in text
    assert "open_code_review_executable=/opt/ocr/bin/opencodereview" in text


@needs_windows
def test_windows_config_update_is_idempotent(tmp_path):
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if not powershell:
        pytest.skip("PowerShell is unavailable")
    source = (ROOT / "install.ps1").read_text(encoding="utf-8")
    function = _function(source, "Set-ConfiguredCodeReview", "ps")
    config = tmp_path / "config.txt"
    config.write_text("open_code_review_enabled=0\nfoo=bar\n", encoding="utf-8")
    escaped = str(config).replace("'", "''")
    script = ('function Write-Warn { param([string]$Message) }\n' + function +
              f"\nSet-ConfiguredCodeReview -ConfigPath '{escaped}' -Executable 'C:\\ocr\\opencodereview.exe' | Out-Null\n" * 2)
    result = subprocess.run([powershell, "-NoProfile", "-Command", script],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    text = config.read_text(encoding="utf-8")
    assert text.count("open_code_review_enabled=") == 1
    assert text.count("open_code_review_executable=") == 1
    assert "open_code_review_enabled=1" in text
