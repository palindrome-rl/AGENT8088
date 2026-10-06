"""The memory-backend choice in both installers, exercised in isolation.

Same extraction convention as test_installer_libreoffice.py: regex one function
out of install.ps1 and run it under pwsh with its dependencies stubbed, rather
than performing a real install. The install.sh half is sourced directly.

Three things worth proving. First, the choice is opt-in and never blocks a host
that cannot answer, because `curl | bash` and CI both run with no console.
Second, a failed mem0 install must degrade to native rather than abort setup --
native needs nothing extra, so a wheel that will not build here is not a reason
to fail the whole install. Third, the engine actually written into config.txt
is the one that got installed: a stale `memory_engine=mem0` with the extra
missing degrades to native silently at runtime, which is the confusion this
whole feature exists to prevent.
"""
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

needs_windows = pytest.mark.skipif(sys.platform != "win32", reason="install.ps1 is Windows-only")


def _powershell() -> str:
    ps = shutil.which("pwsh") or shutil.which("powershell")
    if not ps:
        pytest.skip("PowerShell is not installed")
    return ps


def _powershell_function(name: str) -> str:
    source = (ROOT / "install.ps1").read_text(encoding="utf-8")
    match = re.search(rf"(?ms)^function {re.escape(name)} \{{.*?^\}}$", source)
    assert match, f"PowerShell function not found: {name}"
    return match.group(0)


def _run(body: str, *functions: str) -> str:
    stubs = (
        '$env:CI = $null\n'
        '$env:AGENT8088_INSTALL_MEM0 = $null\n'
        '$NonInteractive = $false\n'
        '$WithMem0 = $false\n'
        '$SkipMem0 = $false\n'
        'function Write-Info { param([string]$Message) }\n'
        'function Write-Success { param([string]$Message) Write-Host "SUCCESS:$Message" }\n'
        'function Write-Warn { param([string]$Message) Write-Host "WARN:$Message" }\n'
        'function Write-StageWarning { param($Result, $TimeoutSec, [string]$What, '
        '[string]$Consequence, [string]$Fix) Write-Host "SKIPPED:$What|$Consequence|$Fix" }\n'
    )
    script = stubs + "\n".join(_powershell_function(f) for f in functions) + "\n" + body
    result = subprocess.run(
        [_powershell(), "-NoProfile", "-Command", script],
        capture_output=True, text=True, check=True,
    )
    return result.stdout


# ---------------------------------------------------------------------------
# install.ps1 -- the consent gate
# ---------------------------------------------------------------------------


@needs_windows
@pytest.mark.parametrize("setup", ['$NonInteractive = $true', '$env:CI = "true"'])
def test_a_host_that_cannot_answer_gets_native_instead_of_hanging(setup):
    """Read-Host returns empty immediately on a headless host, so the explicit
    answer loop would spin forever. Unattended installs must land on native."""
    out = _run(
        setup + '\n'
        'function Read-Host { param($Prompt) "" }\n'
        '$r = Read-Mem0Consent\n'
        'Write-Output "Result=$r"',
        "Read-Mem0Consent",
    )
    assert "Result=False" in out


@needs_windows
def test_prompt_requires_an_explicit_native_or_mem0():
    """Empty input and arbitrary text must re-prompt rather than pick for you."""
    out = _run(
        '$script:answers = @("", "yes", "sqlite", "mem0")\n'
        '$script:i = 0\n'
        'function Read-Host { param($Prompt) $a = $script:answers[$script:i]; $script:i++; '
        'Write-Host "ASKED"; $a }\n'
        '$r = Read-Mem0Consent\n'
        'Write-Output "Result=$r"',
        "Read-Mem0Consent",
    )
    assert out.count("ASKED") == 4
    assert "Result=True" in out


@needs_windows
@pytest.mark.parametrize(
    "value,expected",
    [("1", "True"), ("yes", "True"), ("mem0", "True"),
     ("0", "False"), ("no", "False"), ("native", "False")],
)
def test_env_var_decides_without_reading_the_console(value, expected):
    """`iex (irm ...)` cannot pass a switch, so the env var is the only
    scriptable knob -- same convention as AGENT8088_INSTALL_LIBREOFFICE."""
    out = _run(
        '$env:AGENT8088_INSTALL_MEM0 = "' + value + '"\n'
        'function Read-Host { throw "the env var must decide without prompting" }\n'
        '$r = Read-Mem0Consent\n'
        'Write-Output "Result=$r"',
        "Read-Mem0Consent",
    )
    assert "Result=" + expected in out


@needs_windows
@pytest.mark.parametrize(
    "switch,env_value,expected",
    [("WithMem0", "native", "True"), ("SkipMem0", "mem0", "False")],
)
def test_an_explicit_switch_beats_the_env_var(switch, env_value, expected):
    out = _run(
        f'$env:AGENT8088_INSTALL_MEM0 = "{env_value}"\n'
        '$' + switch + ' = $true\n'
        'function Read-Host { throw "an explicit switch must not prompt" }\n'
        '$r = Read-Mem0Consent\n'
        'Write-Output "Result=$r"',
        "Read-Mem0Consent",
    )
    assert "Result=" + expected in out


@needs_windows
def test_conflicting_switches_are_rejected():
    out = _run(
        '$WithMem0 = $true\n'
        '$SkipMem0 = $true\n'
        'try { $null = Read-Mem0Consent; Write-Output "NO-ERROR" } '
        'catch { Write-Output $_.Exception.Message }',
        "Read-Mem0Consent",
    )
    assert "cannot be used together" in out
    assert "NO-ERROR" not in out


def test_conflicting_switches_are_rejected_before_download_preflight():
    source = (ROOT / "install.ps1").read_text(encoding="utf-8")
    main = source.split("# Main\n# " + "-" * 76 + "\n", 1)[1]
    assert main.index("if ($WithMem0 -and $SkipMem0)") < main.index("Test-DiskSpace")


@needs_windows
@pytest.mark.parametrize("switch", ["WithMem0", "SkipMem0"])
def test_selection_switch_survives_terminal_relaunch(switch):
    other = "SkipMem0" if switch == "WithMem0" else "WithMem0"
    out = _run(
        '$Branch = "development"\n'
        '$Agent8088Home = "C:\\Agent Home"\n'
        '$InstallDir = "C:\\Agent Install"\n'
        '$SkipSetup = $false\n'
        '$TerminalBootstrap = $false\n'
        '$WithLibreOffice = $false\n'
        '$SkipLibreOffice = $false\n'
        '$InstallerSourceUrl = "https://example.test/install.ps1"\n'
        f'${switch} = $true\n'
        f'${other} = $false\n'
        'Write-Output (Get-InstallerInvocation)',
        "ConvertTo-PowerShellLiteral", "Get-InstallerInvocation",
    )
    assert f"-{switch}" in out
    assert f"-{other}" not in out


# ---------------------------------------------------------------------------
# install.ps1 -- the install stage stays optional
# ---------------------------------------------------------------------------


@needs_windows
def test_failed_mem0_install_degrades_instead_of_aborting():
    out = _run(
        '$script:UvCmd = "uv.exe"\n'
        '$TPip = 300\n'
        'function Invoke-WithTimeout { param($FilePath, $Arguments, $TimeoutSec, $Activity) '
        '@{ ExitCode = 1; TimedOut = $false } }\n'
        '$r = Install-Mem0Extra -PythonExe "py.exe" -SourceDir "C:\\src"\n'
        'Write-Output "Result=$r"',
        "Install-Mem0Extra",
    )
    assert "Result=False" in out
    assert "SKIPPED:mem0 memory backend|memory will use the native engine instead" in out
    assert "-WithMem0" in out


@needs_windows
def test_successful_mem0_install_uses_the_extra_and_the_given_python():
    out = _run(
        '$script:UvCmd = "uv.exe"\n'
        '$TPip = 300\n'
        'function Invoke-WithTimeout { param($FilePath, $Arguments, $TimeoutSec, $Activity) '
        'Write-Host "CALL:$FilePath|$($Arguments -join ";")"; @{ ExitCode = 0; TimedOut = $false } }\n'
        '$r = Install-Mem0Extra -PythonExe "py.exe" -SourceDir "C:\\src"\n'
        'Write-Output "Result=$r"',
        "Install-Mem0Extra",
    )
    assert "Result=True" in out
    assert "CALL:uv.exe|pip;install;--python;py.exe;-e;C:\\src[mem0]" in out


# ---------------------------------------------------------------------------
# install.ps1 -- config.txt records the engine that was actually installed
# ---------------------------------------------------------------------------


@needs_windows
def test_config_rewrite_replaces_rather_than_appends(tmp_path):
    config = tmp_path / "config.txt"
    config.write_text("memory=1\nmemory_engine=mem0\nfoo=bar\n", encoding="utf-8")
    out = _run(
        f'$r = Set-ConfiguredMemoryEngine -ConfigPath "{config}" -Engine "native"\n'
        'Write-Output "Result=$r"',
        "Set-ConfiguredMemoryEngine",
    )
    assert "Result=True" in out
    text = config.read_text(encoding="utf-8")
    assert "memory_engine=native" in text
    assert "memory_engine=mem0" not in text
    assert text.count("memory_engine") == 1
    assert "foo=bar" in text


@needs_windows
def test_config_rewrite_appends_when_the_key_is_absent(tmp_path):
    config = tmp_path / "config.txt"
    config.write_text("memory=1\nfoo=bar\n", encoding="utf-8")
    _run(
        f'$null = Set-ConfiguredMemoryEngine -ConfigPath "{config}" -Engine "mem0"',
        "Set-ConfiguredMemoryEngine",
    )
    assert "memory_engine=mem0" in config.read_text(encoding="utf-8")


@needs_windows
def test_config_rewrite_is_idempotent_across_reruns(tmp_path):
    """Rerunning the installer must not stack duplicate memory_engine lines."""
    config = tmp_path / "config.txt"
    config.write_text("memory=1\n", encoding="utf-8")
    for _ in range(3):
        _run(
            f'$null = Set-ConfiguredMemoryEngine -ConfigPath "{config}" -Engine "mem0"',
            "Set-ConfiguredMemoryEngine",
        )
    assert config.read_text(encoding="utf-8").count("memory_engine") == 1


@needs_windows
def test_config_rewrite_writes_no_bom(tmp_path):
    """Windows PowerShell 5's `Set-Content -Encoding UTF8` prepends a UTF-8 BOM.
    The BOM glues itself to the first key, so `\\ufeffmemory_engine=mem0` no longer
    matches `memory_engine` and the engine choice silently falls to native.
    The rewrite must write BOM-free UTF-8, matching the launcher write."""
    config = tmp_path / "config.txt"
    config.write_text("memory=1\n", encoding="utf-8")
    _run(
        f'$null = Set-ConfiguredMemoryEngine -ConfigPath "{config}" -Engine "mem0"',
        "Set-ConfiguredMemoryEngine",
    )
    raw = config.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), "config.txt starts with a UTF-8 BOM"


@needs_windows
def test_a_missing_config_is_reported_not_created(tmp_path):
    missing = tmp_path / "nope.txt"
    out = _run(
        f'$r = Set-ConfiguredMemoryEngine -ConfigPath "{missing}" -Engine "mem0"\n'
        'Write-Output "Result=$r"',
        "Set-ConfiguredMemoryEngine",
    )
    assert "Result=False" in out
    assert not missing.exists()


# ---------------------------------------------------------------------------
# install.sh
# ---------------------------------------------------------------------------


def _bash() -> str:
    sh = shutil.which("bash")
    if not sh:
        pytest.skip("bash is not installed")
    return sh


def _sh_function(name: str) -> str:
    source = (ROOT / "install.sh").read_text(encoding="utf-8")
    match = re.search(rf"(?ms)^{re.escape(name)}\(\) \{{.*?^\}}$", source)
    assert match, f"shell function not found: {name}"
    return match.group(0)


def _run_sh(body: str, *functions: str) -> str:
    # _can_prompt/_read_from_terminal back both resolve_*_choice functions now,
    # so pull them in unconditionally rather than have every call site list them.
    needed = tuple(dict.fromkeys(("_can_prompt", "_read_from_terminal", *functions)))
    preamble = ('IS_INTERACTIVE=false\nMEMORY_CHOICE=""\nLIBREOFFICE_CHOICE=""\n'
                'LIBREOFFICE_INSTALLED=false\nDISTRO="linux"\nCI=""\n'
                'log_info() { :; }\nlog_warn() { :; }\nlog_success() { :; }\n'
                'SKIPPED_STAGES=()\n')
    script = preamble + "\n".join(_sh_function(f) for f in needed) + "\n" + body
    result = subprocess.run([_bash(), "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_sh_non_interactive_defaults_to_native():
    assert _run_sh("resolve_memory_choice", "resolve_memory_choice").strip() == "native"


@pytest.mark.parametrize("value,expected", [
    ("1", "mem0"), ("yes", "mem0"), ("mem0", "mem0"),
    ("0", "native"), ("no", "native"), ("native", "native"),
])
def test_sh_env_var_decides(value, expected):
    out = _run_sh(
        f'AGENT8088_INSTALL_MEM0="{value}"\nresolve_memory_choice',
        "resolve_memory_choice",
    )
    assert out.strip() == expected


def test_sh_flag_beats_the_env_var():
    out = _run_sh(
        'AGENT8088_INSTALL_MEM0="native"\nMEMORY_CHOICE="mem0"\nresolve_memory_choice',
        "resolve_memory_choice",
    )
    assert out.strip() == "mem0"


def test_sh_config_rewrite_replaces_rather_than_appends(tmp_path):
    config = tmp_path / "config.txt"
    config.write_text("memory=1\nmemory_engine=mem0\nfoo=bar\n")
    _run_sh(
        f'set_configured_memory_engine "{config.as_posix()}" native',
        "set_configured_memory_engine",
    )
    text = config.read_text()
    assert "memory_engine=native" in text
    assert text.count("memory_engine") == 1
    assert "foo=bar" in text


def test_sh_config_rewrite_is_idempotent_across_reruns(tmp_path):
    config = tmp_path / "config.txt"
    config.write_text("memory=1\n")
    for _ in range(3):
        _run_sh(
            f'set_configured_memory_engine "{config.as_posix()}" mem0',
            "set_configured_memory_engine",
        )
    assert config.read_text().count("memory_engine") == 1


def test_installers_no_longer_prefetch_a_fastembed_model():
    """Embeddings come from Ollama -- nomic-embed-text is the model the install
    already pulls once for native memory -- so the ~90 MB fastembed prefetch is
    gone from both installers.

    The opt-out (`memory_mem0_embed_provider=fastembed`) downloads its own model
    on first use instead; the runtime still pins FASTEMBED_CACHE_PATH for that
    path, so the model survives the temp-dir purges macOS and Windows both do.
    """
    sh = (ROOT / "install.sh").read_text(encoding="utf-8")
    ps = (ROOT / "install.ps1").read_text(encoding="utf-8")
    for source in (sh, ps):
        assert "from fastembed import" not in source
        assert "Downloading the mem0 embedding model" not in source
    runtime = (ROOT / "src/agent8088/memory/mem0_store.py").read_text(encoding="utf-8")
    assert "FASTEMBED_CACHE_PATH" in runtime
    assert '"fastembed"' in runtime


def test_sh_memory_flag_is_documented_and_validated():
    source = (ROOT / "install.sh").read_text(encoding="utf-8")
    assert "--memory BACKEND" in source
    # An unknown backend must be rejected at parse time, not silently ignored.
    assert "Unknown memory backend" in source


# ---------------------------------------------------------------------------
# install.sh -- piped installs must still be able to ask (the actual bug)
# ---------------------------------------------------------------------------
# `curl -fsSL <url> | bash` is the documented install method. That pipes
# stdin, so IS_INTERACTIVE is false even though a real terminal is sitting
# right there as /dev/tty. Before this fix, resolve_memory_choice checked
# IS_INTERACTIVE directly and always silently fell back to native on exactly
# that path -- the prompt never fired on a real Mac/Linux install. It must
# instead go through _can_prompt, which also tries /dev/tty.


def test_sh_memory_prompt_fires_when_can_prompt_says_yes_even_if_piped():
    out = _run_sh(
        'IS_INTERACTIVE=false\n'
        '_can_prompt() { return 0; }\n'
        '_read_from_terminal() { echo "ASKED" >&2; echo "mem0"; }\n'
        'resolve_memory_choice',
        "resolve_memory_choice",
    )
    assert out.strip() == "mem0"


def test_sh_memory_defaults_to_native_when_no_terminal_is_reachable():
    out = _run_sh(
        '_can_prompt() { return 1; }\n'
        'resolve_memory_choice',
        "resolve_memory_choice",
    )
    assert out.strip() == "native"


# ---------------------------------------------------------------------------
# install.sh -- LibreOffice
# ---------------------------------------------------------------------------


def test_sh_libreoffice_cannot_prompt_defaults_to_false():
    out = _run_sh(
        '_can_prompt() { return 1; }\n'
        'resolve_libreoffice_choice',
        "resolve_libreoffice_choice",
    )
    assert out.strip() == "false"


def test_sh_libreoffice_prompt_fires_even_when_piped():
    """Same bug class as memory: must go through _can_prompt, not IS_INTERACTIVE."""
    out = _run_sh(
        'IS_INTERACTIVE=false\n'
        '_can_prompt() { return 0; }\n'
        '_read_from_terminal() { echo "yes"; }\n'
        'resolve_libreoffice_choice',
        "resolve_libreoffice_choice",
    )
    assert out.strip() == "true"


def test_sh_libreoffice_reprompts_on_empty_answer():
    out = _run_sh(
        '_can_prompt() { return 0; }\n'
        'echo 0 > /tmp/_lo_idx.$$\n'
        '_read_from_terminal() {\n'
        '  n=$(cat /tmp/_lo_idx.$$); n=$((n+1)); echo "$n" > /tmp/_lo_idx.$$\n'
        '  echo "ASKED-$n" >&2\n'
        '  if [ "$n" -eq 1 ]; then echo ""; else echo "no"; fi\n'
        '}\n'
        'resolve_libreoffice_choice\n'
        'rm -f /tmp/_lo_idx.$$',
        "resolve_libreoffice_choice",
    )
    assert out.strip().splitlines()[-1] == "false"


def test_sh_libreoffice_termux_never_prompts():
    out = _run_sh(
        'DISTRO="termux"\n'
        '_can_prompt() { echo "MUST NOT BE CALLED" >&2; return 0; }\n'
        'resolve_libreoffice_choice',
        "resolve_libreoffice_choice",
    )
    assert out.strip() == "false"


@pytest.mark.parametrize("value,expected", [
    ("1", "true"), ("yes", "true"), ("true", "true"),
    ("0", "false"), ("no", "false"), ("false", "false"),
])
def test_sh_libreoffice_env_var_decides(value, expected):
    out = _run_sh(
        f'AGENT8088_INSTALL_LIBREOFFICE="{value}"\nresolve_libreoffice_choice',
        "resolve_libreoffice_choice",
    )
    assert out.strip() == expected


def test_sh_libreoffice_flag_beats_the_env_var():
    out = _run_sh(
        'AGENT8088_INSTALL_LIBREOFFICE="true"\nLIBREOFFICE_CHOICE="false"\n'
        'resolve_libreoffice_choice',
        "resolve_libreoffice_choice",
    )
    assert out.strip() == "false"


def test_sh_libreoffice_flags_are_documented():
    source = (ROOT / "install.sh").read_text(encoding="utf-8")
    assert "--with-libreoffice" in source
    assert "--skip-libreoffice" in source


def test_sh_install_libreoffice_failure_is_non_fatal():
    """A failed or unsupported install must record a skipped stage, never abort."""
    out = _run_sh(
        'command() { [ "$1" = "-v" ] && [ "$2" = "soffice" ] && return 1; builtin command "$@"; }\n'
        'OS="freebsd"\n'  # unrecognized -> falls through every branch
        'run_with_timeout() { shift; "$@"; }\n'
        'install_libreoffice\n'
        'echo "STAGES=${#SKIPPED_STAGES[@]}"',
        "install_libreoffice", "record_skip", "warn_stage",
    )
    assert "STAGES=1" in out


def test_sh_install_libreoffice_uses_brew_cask_on_macos(tmp_path):
    # install_libreoffice redirects the install command's own stdout to
    # /dev/null (matching every other optional-extra block), so the stub
    # writes its record to a file instead of stdout.
    marker = tmp_path / "call.txt"
    out = _run_sh(
        'OS="macos"\n'
        'command() { [ "$1" = "-v" ] && [ "$2" = "soffice" ] && return 1; '
        '[ "$1" = "-v" ] && [ "$2" = "brew" ] && return 0; builtin command "$@"; }\n'
        f'run_with_timeout() {{ shift; echo "CALL:$*" > "{marker.as_posix()}"; "$@"; }}\n'
        'brew() { return 0; }\n'
        'install_libreoffice\n'
        'echo "INSTALLED=$LIBREOFFICE_INSTALLED"',
        "install_libreoffice",
    )
    assert marker.read_text().strip() == "CALL:brew install --cask libreoffice"
    # soffice still absent after a "successful" brew run (no real binary in
    # this stub) -- the function must not claim success it did not verify.
    assert "INSTALLED=false" in out


def test_sh_install_libreoffice_skips_when_already_present():
    out = _run_sh(
        'command() { [ "$1" = "-v" ] && [ "$2" = "soffice" ] && return 0; builtin command "$@"; }\n'
        'install_libreoffice\n'
        'echo "INSTALLED=$LIBREOFFICE_INSTALLED"',
        "install_libreoffice",
    )
    assert "INSTALLED=true" in out
