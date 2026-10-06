"""Compatibility boundaries for the installation-only public backport."""
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
PUBLIC = "palindrome-rl/AGENT8088"
BRANCH = "AGENT8088-v1.2"


@pytest.mark.parametrize("name", ["install.ps1", "install.sh"])
def test_install_and_recovery_stay_on_the_public_release(name):
    source = (ROOT / name).read_text(encoding="utf-8")
    assert PUBLIC in source and BRANCH in source
    assert "RT-Internal-DS" not in source
    assert "staging-1.2" not in source
    assert "agent8088-installer.pages.dev" not in source
    assert "agent8088 --doctor" not in source
    assert "private repository" not in source
    assert "repo scope" not in source


def test_release_version_license_and_update_channel_are_preserved():
    metadata = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'version = "1.2.0"' in metadata
    assert 'license = "MIT"' in metadata
    cli = (ROOT / "src/agent8088/cli.py").read_text(encoding="utf-8")
    assert 'UPDATE_BRANCH = "AGENT8088-v1.2"' in cli


def test_windows_config_acl_uses_absolute_system_binary():
    source = (ROOT / "install.ps1").read_text(encoding="utf-8")
    body = re.search(r"(?ms)^function Protect-ConfigFile \{.*?^\}", source).group()
    assert 'Join-Path $env:SystemRoot "System32\\icacls.exe"' in body
    assert "& $icacls" in body
    assert "grant:r" in body and "/inheritance:r" in body


@pytest.mark.skipif(os.name != "nt", reason="Windows ACLs")
def test_config_acl_works_even_when_system32_is_not_on_path(tmp_path):
    host = shutil.which("powershell") or shutil.which("pwsh")
    if not host:
        pytest.skip("PowerShell not installed")
    source = (ROOT / "install.ps1").read_text(encoding="utf-8")
    body = re.search(r"(?ms)^function Protect-ConfigFile \{.*?^\}", source).group()
    config = tmp_path / "Config With Spaces.txt"
    config.write_text("test-only=true\n", encoding="utf-8")
    quoted = str(config).replace("'", "''")
    command = body + "\n$env:Path = ''; Protect-ConfigFile '" + quoted + "'; Write-Output 'SECURED'"
    result = subprocess.run([host, "-NoProfile", "-Command", command],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SECURED" in result.stdout
    assert config.read_text(encoding="utf-8") == "test-only=true\n"


def test_diagnostics_do_not_require_unshipped_runtime_modules():
    for name in ("install.ps1", "install.sh"):
        source = (ROOT / name).read_text(encoding="utf-8")
        assert "agent8088/install_state.py" not in source
        assert "does not load this ledger in the agent" in source


def test_linux_retry_command_uses_the_public_default_branch():
    source = (ROOT / "install.sh").read_text(encoding="utf-8")
    assert 'REPO_BRANCH="${AGENT8088_BRANCH:-AGENT8088-v1.2}"' in source
    assert 'if [ "$BRANCH" = "AGENT8088-v1.2" ]; then' in source
