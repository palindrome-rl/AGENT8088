"""install-state.json: the installer's skipped-stage ledger, persisted.

Same convention as test_installer_error_handling.py: pull individual functions
out of install.sh / install.ps1 by regex and run them under a bare shell with
their dependencies stubbed. Nothing here runs an installer or touches the real
HOME -- AGENT8088_HOME is always under tmp_path.
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BASH = shutil.which("bash") or "/bin/bash"

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX shell")

STUB_LOGS = (
    'log_info() { echo "INFO:$1"; }\n'
    'log_warn() { echo "WARN:$1"; }\n'
    'log_error() { echo "ERR:$1"; }\n'
)


def _sh_function(name: str) -> str:
    source = (ROOT / "install.sh").read_text(encoding="utf-8")
    match = re.search(rf"(?ms)^{re.escape(name)}\(\) \{{.*?^\}}$", source)
    assert match, f"shell function not found in install.sh: {name}"
    return match.group(0)


def _run_sh(tmp_path: Path, body: str, *functions: str, bash: str = BASH):
    script = tmp_path / "harness.sh"
    script.write_text(
        STUB_LOGS + "\n".join(_sh_function(f) for f in functions) + "\n" + body + "\n",
        encoding="utf-8",
    )
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(tmp_path)}
    return subprocess.run([bash, str(script)], capture_output=True, text=True,
                          env=env, timeout=60)


NASTY = 'quote " backslash \\ newline\nline2 cr\r tab\t ctrl\x01\x1f del\x7f utf8 é'


def _bash_quote(text: str) -> str:
    """$'...' literal for `text` (works in bash 3.2)."""
    out = []
    for ch in text:
        if ch in "\\'":
            out.append("\\" + ch)
        elif ord(ch) < 32 or ord(ch) == 0x7f:
            out.append("\\x%02x" % ord(ch))
        else:
            out.append(ch)
    return "$'" + "".join(out) + "'"


def _bashes():
    found = []
    for candidate in ("/bin/bash", shutil.which("bash")):
        if candidate and os.path.exists(candidate) and candidate not in found:
            found.append(candidate)
    return found


@posix_only
@pytest.mark.parametrize("bash", _bashes())
def test_json_escape_round_trips_quotes_backslashes_and_control_chars(tmp_path, bash):
    result = _run_sh(tmp_path, f'printf \'"%s"\' "$(_json_escape {_bash_quote(NASTY)})"',
                     "_json_escape", bash=bash)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == NASTY


@posix_only
@pytest.mark.parametrize("bash", _bashes())
def test_write_install_state_persists_the_ledger(tmp_path, bash):
    home = tmp_path / "home"
    home.mkdir()
    body = (
        f'AGENT8088_HOME="{home}"\n'
        "SKIPPED_STAGES=()\n"
        f'record_skip "Chromium browser" {_bash_quote(NASTY)} "python -m playwright install chromium"\n'
        'record_skip "Empty reason" "" "fix it"\n'
        'record_skip "No fix" "failed (exit 1)"\n'
        "write_install_state\n"
    )
    result = _run_sh(tmp_path, body, "_json_escape", "record_skip", "write_install_state", bash=bash)
    assert result.returncode == 0, result.stderr
    data = json.loads((home / "install-state.json").read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", data["installed_at"])
    # A tab inside a field would split it (the ledger is tab-separated), so the
    # nasty text minus its tab is what must survive intact.
    assert data["skipped"] == [
        {"stage": "Chromium browser", "reason": NASTY.split("\t")[0],
         "fix": NASTY.split("\t", 1)[1] + "\tpython -m playwright install chromium"},
        {"stage": "Empty reason", "reason": "", "fix": "fix it"},
        {"stage": "No fix", "reason": "failed (exit 1)", "fix": ""},
    ]
    assert not list(home.glob("install-state.json.tmp.*"))


@posix_only
def test_write_install_state_overwrites_so_fixed_stages_disappear(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "install-state.json").write_text(
        '{"version": 1, "installed_at": "x", "skipped": [{"stage": "Old", "reason": "", "fix": ""}]}')
    body = f'AGENT8088_HOME="{home}"\nSKIPPED_STAGES=()\nwrite_install_state\n'
    result = _run_sh(tmp_path, body, "_json_escape", "write_install_state")
    assert result.returncode == 0, result.stderr
    assert json.loads((home / "install-state.json").read_text())["skipped"] == []


@posix_only
def test_write_install_state_failure_warns_and_never_fails(tmp_path):
    body = f'AGENT8088_HOME="{tmp_path / "missing" / "dir"}"\nSKIPPED_STAGES=()\nset -e\nwrite_install_state\necho DONE\n'
    result = _run_sh(tmp_path, body, "_json_escape", "write_install_state")
    assert result.returncode == 0
    assert "WARN:Could not save the skipped-stage diagnostics" in result.stdout
    assert "DONE" in result.stdout


def test_summary_writes_the_state_before_printing():
    source = (ROOT / "install.sh").read_text(encoding="utf-8")
    assert re.search(r"write_install_state\n\s*print_skipped_summary", source)


# --------------------------------------------------------------------------
# install.ps1
# --------------------------------------------------------------------------
def _powershell() -> str:
    ps = shutil.which("pwsh") or shutil.which("powershell")
    if not ps:
        pytest.skip("PowerShell is not installed")
    return ps


def _ps_function(name: str) -> str:
    source = (ROOT / "install.ps1").read_text(encoding="utf-8")
    match = re.search(rf"(?ms)^function {re.escape(name)} \{{.*?^\}}\r?$", source)
    assert match, f"PowerShell function not found: {name}"
    return match.group(0)


def _ps_literal(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


@pytest.mark.parametrize("count", [0, 1, 2])
def test_ps1_save_install_state_writes_a_json_array_without_bom(tmp_path, count):
    fix_prefix = "C:\\fix "
    stages = "\n".join(
        "Register-SkippedStage -Label " + _ps_literal(f"Stage {i}")
        + " -Reason " + _ps_literal(NASTY) + " -Fix " + _ps_literal(fix_prefix + str(i))
        for i in range(count))
    script = (
        'function Write-Warn { param([string]$Message) Write-Host "WARN:$Message" }\n'
        + _ps_function("Register-SkippedStage") + "\n"
        + _ps_function("Save-InstallState") + "\n"
        + f"$Agent8088Home = {_ps_literal(str(tmp_path))}\n"
        + "$script:SkippedStages = $null\n"
        + stages + "\nSave-InstallState\n"
    )
    result = subprocess.run([_powershell(), "-NoProfile", "-Command", script],
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    raw = (tmp_path / "install-state.json").read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    data = json.loads(raw.decode("utf-8"))
    assert data["version"] == 1
    assert data["skipped"] == [
        {"stage": f"Stage {i}", "reason": NASTY, "fix": fix_prefix + str(i)} for i in range(count)]


def test_ps1_summary_saves_state_even_when_nothing_was_skipped():
    body = _ps_function("Write-SkippedSummary")
    save = body.index("Save-InstallState")
    early_return = body.index("{ return }")
    assert save < early_return


def test_ps1_stays_crlf_and_ascii():
    raw = (ROOT / "install.ps1").read_bytes()
    raw.decode("ascii")
    assert raw.count(b"\n") == raw.count(b"\r\n")
