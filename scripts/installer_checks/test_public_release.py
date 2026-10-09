"""Compatibility boundaries for the public v1.2 release."""
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
PUBLIC = "palindrome-rl/AGENT8088"
BRANCH = "AGENT8088-v1.2"
# Third-party repositories the Windows installer legitimately downloads from.
VENDOR = {"astral-sh/uv", "git-for-windows/git", "microsoft/terminal"}
_SLUG = re.compile(r"(?:github\.com|githubusercontent\.com)/([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)")


@pytest.mark.parametrize("name", ["install.ps1", "install.sh"])
def test_install_and_recovery_stay_on_the_public_release(name):
    """Every repository an installer reaches for is the public one.

    Enumerated rather than checked against a list of names that must not
    appear: a slug this test has never heard of still fails here, which a
    deny-list cannot do."""
    source = (ROOT / name).read_text(encoding="utf-8")
    assert PUBLIC in source and BRANCH in source
    slugs = {slug.removesuffix(".git") for slug in _SLUG.findall(source)}
    assert slugs - VENDOR == {PUBLIC}, sorted(slugs)
    # A public clone is anonymous: no credential prompt, no token scope.
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


def test_diagnostics_have_their_shipped_runtime_modules():
    for name in ("install_state", "capabilities", "errors"):
        assert (ROOT / "src/agent8088" / (name + ".py")).is_file()
    for name in ("install.ps1", "install.sh"):
        source = (ROOT / name).read_text(encoding="utf-8")
        assert "does not load this ledger in the agent" not in source
        assert "agent8088 --doctor" in source


def test_linux_retry_command_uses_the_public_default_branch():
    source = (ROOT / "install.sh").read_text(encoding="utf-8")
    assert 'REPO_BRANCH="${AGENT8088_BRANCH:-AGENT8088-v1.2}"' in source
    assert 'if [ "$BRANCH" = "AGENT8088-v1.2" ]; then' in source
