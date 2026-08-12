"""The installer's progress bar, exercised as PowerShell rather than as text.

Every long stage used to be piped to Out-Null, so the console sat on a single
line for minutes -- Chromium alone is ~280 MB -- with no way to tell a slow
download from a hang, and the child's diagnostics were discarded along with its
output.

These run the real functions out of install.ps1. The installer body is never
executed: only the function definitions are lifted from the parsed AST, so
loading them cannot clone a repo, create a venv or download anything. The child
process is always a throwaway `python -c`.

Text assertions would not have earned their keep here. Two defects survived
review of this code and were caught only by running it:

  * Start-Process -PassThru leaves .ExitCode as $null unless the process handle
    is touched first, so every stage reported failure and returned nothing.
  * -ArgumentList joins an array without quoting, so the first path containing
    a space arrived at the child split into separate arguments -- and every one
    of these paths comes from $env:LOCALAPPDATA, which contains a space
    whenever the account name does.
"""
import re
import runpy
import subprocess
import sys
import threading
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32",
                                reason="install.ps1 is the Windows installer")

INSTALLER = Path(__file__).resolve().parent.parent / "install.ps1"
RELEASE_CHECK = Path(__file__).resolve().parent.parent / "scripts" / "release_check.py"

# Lift the named functions out of the AST and declare them. Parsing never runs
# the script, so none of the installer's stages execute.
_HARNESS = """
$ErrorActionPreference = "Stop"
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    "{installer}", [ref]$null, [ref]$null)
$wanted = @("Test-ProgressAnimated", "Format-ProgressBar", "Format-ProgressSweep",
            "Get-ReportedPercent", "Invoke-WithProgress", "ConvertTo-ArgumentString",
            "Get-ProgressLineWidth", "Format-ProgressLine", "Write-Info", "Write-Warn")
foreach ($fn in $ast.FindAll({{
    $args[0] -is [System.Management.Automation.Language.FunctionDefinitionAst] }}, $true)) {{
    if ($wanted -contains $fn.Name) {{ . ([scriptblock]::Create($fn.Extent.Text)) }}
}}
$script:ProgressBarWidth = 24
{body}
"""


def _powershell(body):
    """Run *body* with the installer's progress functions in scope."""
    script = _HARNESS.format(installer=str(INSTALLER).replace("\\", "\\\\"), body=body)
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, f"harness failed: {result.stderr[:400]}"
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# the bar itself
# ---------------------------------------------------------------------------
def test_the_bar_fills_in_proportion_to_the_percentage():
    assert _powershell("Write-Output (Format-ProgressBar 0)") == "[" + "." * 24 + "]"
    assert _powershell("Write-Output (Format-ProgressBar 50)") == "[" + "#" * 12 + "." * 12 + "]"
    assert _powershell("Write-Output (Format-ProgressBar 100)") == "[" + "#" * 24 + "]"


def test_an_out_of_range_percentage_cannot_deform_the_bar():
    """A malformed line from a child must not draw a bar wider than the field."""
    assert _powershell("Write-Output (Format-ProgressBar 999)") == "[" + "#" * 24 + "]"
    assert _powershell("Write-Output (Format-ProgressBar -20)") == "[" + "." * 24 + "]"


def test_the_indeterminate_sweep_keeps_the_bar_width_constant():
    """The line must not change shape when a percentage finally appears."""
    widths = _powershell(
        "0..40 | ForEach-Object { (Format-ProgressSweep $_).Length } | Sort-Object -Unique"
    ).split()
    assert widths == [str(len("[" + "." * 24 + "]"))]


# ---------------------------------------------------------------------------
# reading progress back out of the child's output
# ---------------------------------------------------------------------------
def test_a_reported_percentage_is_read_from_the_childs_output(tmp_path):
    log = tmp_path / "child.log"
    log.write_text("Downloading Chromium\n|####    | 42% of 280 MB\n", encoding="utf-8")

    assert _powershell(
        f'Write-Output (Get-ReportedPercent -Paths @("{log}") -Fallback -1)') == "42"


def test_the_bar_never_runs_backwards(tmp_path):
    """A tail can straddle two bars: pip finishing one package as another starts."""
    log = tmp_path / "child.log"
    log.write_text("|##  | 5% of the next file\n", encoding="utf-8")

    assert _powershell(
        f'Write-Output (Get-ReportedPercent -Paths @("{log}") -Fallback 80)') == "80"


def test_output_with_no_percentage_leaves_the_bar_indeterminate(tmp_path):
    log = tmp_path / "child.log"
    log.write_text("Resolving dependencies\n", encoding="utf-8")

    assert _powershell(
        f'Write-Output (Get-ReportedPercent -Paths @("{log}") -Fallback -1)') == "-1"


def test_a_log_the_child_still_holds_open_is_survivable(tmp_path):
    """Polling runs ~8x a second against a file the child is writing."""
    assert _powershell(
        f'Write-Output (Get-ReportedPercent -Paths @("{tmp_path / "absent.log"}") -Fallback 7)'
    ) == "7"


# ---------------------------------------------------------------------------
# running an actual child process
# ---------------------------------------------------------------------------
def _run_child(exit_code, extra=""):
    """Invoke-WithProgress against a throwaway python child, animation forced on.

    Output is redirected under pytest, which would otherwise select the plain
    fallback and leave the animated path untested.
    """
    return _powershell(
        'function Test-ProgressAnimated { $true }\n'
        f'$code = Invoke-WithProgress -Label "test" -FilePath "{sys.executable}" '
        f'-ArgumentList @("-c", "import sys; sys.exit({exit_code})"{extra})\n'
        'Write-Output "EXIT=$code"'
    ).splitlines()[-1]


def test_a_successful_stage_reports_success():
    """Start-Process -PassThru returns $null for .ExitCode unless the handle is
    cached first, which made every stage look like a failure."""
    assert _run_child(0) == "EXIT=0"


def test_a_failing_stage_propagates_the_childs_exit_code():
    """Callers branch on this to warn-and-continue or abort."""
    assert _run_child(3) == "EXIT=3"


def test_an_argument_containing_a_space_survives_intact(tmp_path):
    """LOCALAPPDATA contains a space whenever the account name does."""
    spaced = tmp_path / "dir with space"
    spaced.mkdir()
    marker = spaced / "marker.txt"
    quoted = str(marker).replace("\\", "\\\\").replace('"', '')

    out = _powershell(
        'function Test-ProgressAnimated { $true }\n'
        f'$code = Invoke-WithProgress -Label "test" -FilePath "{sys.executable}" '
        f"-ArgumentList @(\"-c\", \"open(r'{marker}','w').write('ok')\")\n"
        'Write-Output "EXIT=$code"'
    )

    assert out.splitlines()[-1] == "EXIT=0"
    assert marker.read_text() == "ok", "the path was split on its space"
    assert quoted  # the path really did contain a space


def _run_script(script, args='@("install")'):
    return _powershell(
        'function Test-ProgressAnimated { $true }\n'
        f'$code = Invoke-WithProgress -Label "npm" -FilePath "{script}" '
        f'-ArgumentList {args}\n'
        'Write-Output "EXIT=$code"'
    ).splitlines()[-1]


def test_a_powershell_script_runs(tmp_path):
    """The exact shape that broke the WhatsApp bridge stage.

    A standard Node install ships npm three ways side by side -- npm (bash),
    npm.cmd and npm.ps1 -- and Get-Command resolves to npm.ps1. The call
    operator ran that in-process; Start-Process cannot, because CreateProcess
    has no image to load and redirected streams rule out
    ShellExecute. It failed with "%1 is not a valid Win32 application".
    """
    script = tmp_path / "fake_npm.ps1"
    script.write_text("Write-Output 'installing'\nexit 0\n", encoding="ascii")

    assert _run_script(script) == "EXIT=0"


def test_a_failing_powershell_script_reports_its_exit_code(tmp_path):
    script = tmp_path / "fake_npm.ps1"
    script.write_text("exit 7\n", encoding="ascii")

    assert _run_script(script) == "EXIT=7"


def test_a_batch_file_runs(tmp_path):
    """npm.cmd is what Resolve-NpmLauncher now prefers, so it has to work."""
    script = tmp_path / "fake_npm.cmd"
    script.write_text("@echo off\r\necho installing\r\nexit /b 0\r\n", encoding="ascii")

    assert _run_script(script) == "EXIT=0"


def test_a_script_in_a_path_with_spaces_runs(tmp_path):
    """Program Files is where Node actually lives."""
    directory = tmp_path / "node dir"
    directory.mkdir()
    script = directory / "fake npm.ps1"
    marker = directory / "ran.txt"
    script.write_text(f"'ok' | Set-Content -LiteralPath '{marker}'\nexit 0\n", encoding="ascii")

    assert _run_script(script) == "EXIT=0"
    assert marker.exists(), "the script never actually ran"


def test_npm_is_resolved_to_something_start_process_can_launch():
    """Get-Command alone returns npm.ps1; the launcher must prefer npm.cmd."""
    installer = INSTALLER.read_text(encoding="utf-8")

    assert "function Resolve-NpmLauncher" in installer
    assert '$npmExe = (Get-Command npm' not in installer, \
        "back to the .ps1 that Start-Process cannot launch"
    assert installer.count("$npmExe = Resolve-NpmLauncher") == 2


def test_a_stage_is_given_no_access_to_the_console_input():
    """Console modes belong to the console, not to a process.

    A child that inherits the input handle and alters it leaves the console
    altered for everything after it. Sharing it corrupted the setup wizard
    running later in the same window -- keystrokes were dropped, so a typed
    path arrived as "C   sers saa   a mi" and the API key arrived wrong -- and
    left the agent's display broken in that window too. The piped form this
    replaced never handed the child a console at all.
    """
    installer = INSTALLER.read_text(encoding="utf-8")

    assert "-RedirectStandardInput $inLog" in installer, \
        "stages are sharing the user's console input again"


def test_a_progress_child_cannot_modify_the_installers_console():
    """The progress child needs no console because all three streams are
    redirected. A shared console lets it corrupt input/output modes globally."""
    installer = INSTALLER.read_text(encoding="utf-8")
    progress = installer[
        installer.index("function Invoke-WithProgress"):installer.index("function Protect-ConfigFile")
    ]

    assert "-WindowStyle Hidden" in progress
    assert "-NoNewWindow" not in progress


def test_agent_startup_repairs_only_missing_console_flags(tmp_path, monkeypatch):
    """An already-corrupted PowerShell window must be usable without requiring
    the user to discover that closing and reopening it repairs the symptom."""
    monkeypatch.setenv("AGENT8088_CONFIG", str(tmp_path / "missing-config.txt"))
    monkeypatch.setenv("AGENT8088_HOME", str(tmp_path))
    from agent8088 import cli

    modes = {10: 0, 20: 0x0040}
    writes = []

    class Stream:
        def __init__(self, handle):
            self.handle = handle

        def fileno(self):
            return self.handle

        def isatty(self):
            return True

    monkeypatch.setattr(cli.os, "name", "nt")
    monkeypatch.setattr(cli.sys, "stdin", Stream(10))
    monkeypatch.setattr(cli.sys, "stdout", Stream(20))
    monkeypatch.setattr(
        cli,
        "_windows_console_functions",
        lambda: (
            lambda stream: stream.handle,
            lambda handle: modes[handle],
            lambda handle, mode: writes.append((handle, mode)),
        ),
    )

    assert cli._repair_windows_console() is True
    assert writes == [(10, 0x0007), (20, 0x0047)]


def test_windows_repl_preserves_known_good_prompt_layout(
        tmp_path, monkeypatch):
    """The prompt must not add vertical space above the sticky footer."""
    monkeypatch.setenv("AGENT8088_CONFIG", str(tmp_path / "missing-config.txt"))
    monkeypatch.setenv("AGENT8088_HOME", str(tmp_path))
    from agent8088 import cli
    import prompt_toolkit

    captured = {}
    footer_events = []

    class Tty:
        @staticmethod
        def isatty():
            return True

    monkeypatch.setattr(cli.sys, "stdin", Tty())
    monkeypatch.setattr(
        cli, "_response_footer",
        SimpleNamespace(
            stop=lambda: footer_events.append("stop"),
            start=lambda state: footer_events.append(("start", state)),
        ),
    )
    monkeypatch.setattr(
        prompt_toolkit,
        "prompt",
        lambda message, **kwargs: captured.update(kwargs, message=message) or "hello",
    )

    assert cli._read_line() == "hello"
    assert "ready" in "".join(text for _, text in captured["bottom_toolbar"]())
    assert captured["reserve_space_for_menu"] == 0
    assert not captured["message"].value.startswith("\n")
    assert footer_events == ["stop", ("start", "ready")]


@pytest.mark.parametrize(("width", "height"), [(50, 10), (100, 30), (190, 50)])
def test_response_footer_reserves_the_last_terminal_row(
        width, height, tmp_path, monkeypatch):
    """Submitting a prompt must not remove the footer while Rich streams."""
    monkeypatch.setenv("AGENT8088_CONFIG", str(tmp_path / "missing-config.txt"))
    monkeypatch.setenv("AGENT8088_HOME", str(tmp_path))
    from agent8088 import cli
    from rich.console import Console

    output = StringIO()
    output.isatty = lambda: True
    monkeypatch.setattr(
        cli, "console",
        Console(file=output, width=width, height=height, force_terminal=True),
    )

    footer = cli._PersistentStatusBar()
    footer.start("working")
    footer.stop()

    rendered = output.getvalue()
    assert f"\x1b[1;{height - 1}r" in rendered
    assert f"\x1b[{height};1H" in rendered
    assert "8088" in rendered
    if width == 190:
        assert "working" in rendered
    assert "\x1b[r" in rendered


def test_response_footer_does_not_race_unchanged_live_updates(
        tmp_path, monkeypatch):
    """Rich owns a refresh thread; an unchanged footer must not keep moving the
    same cursor after every streamed token. That race clipped line prefixes and
    assembled the footer from fragments in Windows Terminal."""
    monkeypatch.setenv("AGENT8088_CONFIG", str(tmp_path / "missing-config.txt"))
    monkeypatch.setenv("AGENT8088_HOME", str(tmp_path))
    from agent8088 import cli
    from rich.console import Console

    output = StringIO()
    output.isatty = lambda: True
    monkeypatch.setattr(
        cli, "console",
        Console(file=output, width=100, height=30, force_terminal=True),
    )

    footer = cli._PersistentStatusBar()
    footer.start("working")
    first = output.getvalue()
    for _ in range(100):
        footer.refresh("working")

    assert output.getvalue() == first


def test_response_footer_serializes_cursor_controls_with_rich(
        tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT8088_CONFIG", str(tmp_path / "missing-config.txt"))
    monkeypatch.setenv("AGENT8088_HOME", str(tmp_path))
    from agent8088 import cli

    state = threading.local()
    writes = []

    class TrackingLock:
        def __enter__(self):
            state.held = True

        def __exit__(self, *_args):
            state.held = False

    class Stream:
        @staticmethod
        def isatty():
            return True

        @staticmethod
        def flush():
            assert state.held

        @staticmethod
        def write(value):
            assert state.held, "footer cursor controls raced Rich Live"
            writes.append(value)

    fake_console = SimpleNamespace(
        file=Stream(), is_terminal=True, width=80, height=20,
        _lock=TrackingLock(),
    )
    monkeypatch.setattr(cli, "console", fake_console)

    footer = cli._PersistentStatusBar()
    footer.start("working")
    footer.stop()

    assert writes


def test_response_footer_resize_clears_its_former_row(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT8088_CONFIG", str(tmp_path / "missing-config.txt"))
    monkeypatch.setenv("AGENT8088_HOME", str(tmp_path))
    from agent8088 import cli
    from rich.console import Console

    output = StringIO()
    output.isatty = lambda: True
    terminal = Console(file=output, width=80, height=10, force_terminal=True)
    monkeypatch.setattr(cli, "console", terminal)
    footer = cli._PersistentStatusBar()
    footer.start("working")
    output.seek(0)
    output.truncate(0)

    terminal._height = 12
    footer.refresh("working")

    rendered = output.getvalue()
    assert "\x1b[10;1H\x1b[2K" in rendered
    assert "\x1b[1;11r" in rendered
    assert "\x1b[12;1H" in rendered


def test_live_response_updates_keep_the_footer_current(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT8088_CONFIG", str(tmp_path / "missing-config.txt"))
    monkeypatch.setenv("AGENT8088_HOME", str(tmp_path))
    from agent8088 import cli

    updates = []
    refreshes = []
    live = SimpleNamespace(update=lambda value, **kwargs: updates.append((value, kwargs)))
    footer = SimpleNamespace(refresh=lambda state="working": refreshes.append(state))

    cli._LiveWithFooter(live, footer).update("partial response", refresh=True)

    assert updates == [("partial response", {"refresh": True})]
    assert refreshes == ["working"]


@pytest.mark.parametrize(("width", "height"), [(60, 16), (100, 30), (190, 50)])
def test_real_conpty_keeps_complete_text_above_the_footer(tmp_path, width, height):
    """Exercise the real Prompt Toolkit -> Rich Live -> plan prompt sequence.

    This is optional in the ordinary suite because pywinpty/pyte are test-only;
    the release check runs it explicitly with those transient packages.
    """
    winpty = pytest.importorskip("winpty")
    pyte = pytest.importorskip("pyte")
    config = tmp_path / "config.txt"
    config.write_text(f"allowed_paths={tmp_path}\n", encoding="utf-8")
    child = """
import time
from rich.live import Live
from rich.panel import Panel
from agent8088 import cli

answer = cli._read_line()
cli._response_footer.start("working")
with Live(console=cli.console, refresh_per_second=40, transient=True) as raw:
    live = cli._LiveWithFooter(raw, cli._response_footer)
    for index in range(4):
        cli.console.print(f"Backend Role Ready row-{index:02d}")
        live.update(Panel(f"response-{index:02d} complete text", title="Agent8088"), refresh=True)
        time.sleep(0.01)
    live.stop()
    cli.console.print(Panel("1 Write library.py\\n2 Verify the script", title="Plan"))
    choice = cli.console.input("[yellow]Approve plan?[/yellow] ")
    live.start()
    for index in range(4, 8):
        cli.console.print(f"Verify complete line-{index:02d}")
        live.update(Panel(f"working-{index:02d}", title="Agent8088"), refresh=True)
        time.sleep(0.01)
cli.console.print(f"FINAL ANSWER {answer} {choice}")
cli._response_footer.refresh("ready")
cli._response_footer.stop()
"""
    env = dict(__import__("os").environ)
    env["AGENT8088_CONFIG"] = str(config)
    env["AGENT8088_HOME"] = str(tmp_path)
    process = winpty.PtyProcess.spawn(
        [sys.executable, "-c", child], dimensions=(height, width), env=env,
        cwd=str(INSTALLER.parent),
    )
    process.write("hi\r")
    screen = pyte.Screen(width, height)
    stream = pyte.Stream(screen)
    raw_output = []
    approved = False
    while True:
        try:
            chunk = process.read(4096)
        except EOFError:
            break
        if not chunk:
            break
        raw_output.append(chunk)
        stream.feed(chunk)
        if not approved and "Approve plan?" in "".join(raw_output):
            process.write("a\r")
            approved = True

    visible = "\n".join(screen.display)
    assert approved
    assert "FINAL ANSWER hi a" in visible
    assert "Verify complete line-07" in visible
    assert screen.display[-1].lstrip().startswith("◆ 8088")
    for line in screen.display:
        if "complete line-" in line:
            assert line.lstrip().startswith("Verify complete line-")


def test_setup_model_discovery_has_a_short_timeout_and_no_retries(
        tmp_path, monkeypatch, capsys):
    """A dead custom endpoint must fall back to model entry instead of making
    setup look frozen behind the OpenAI SDK's retry policy."""
    monkeypatch.setenv("AGENT8088_CONFIG", str(tmp_path / "config.txt"))
    monkeypatch.setenv("AGENT8088_HOME", str(tmp_path))
    from agent8088 import cli, providers

    config = tmp_path / "config.txt"
    config.write_text(
        "default_provider=openai\nprovider.openai.model=typed-model\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cli.A, "ENV_FILE_PATH", tmp_path / ".env")
    monkeypatch.setattr(cli, "_choice_prompt", lambda *_args, **_kwargs: "openai")
    monkeypatch.setattr(
        cli,
        "_custom_prompt",
        lambda message, *_args, **_kwargs: (
            "key" if message.startswith("API key") else "typed-model"
        ),
    )
    created = {}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            created.update(kwargs)

    monkeypatch.setattr("openai.OpenAI", FakeOpenAI)

    monkeypatch.setattr(providers, "list_models", lambda *_args, **_kwargs: [])

    cli._run_setup(config_path=config, include_workspace=False)

    assert created["timeout"] == cli.MODEL_DISCOVERY_TIMEOUT_SECONDS
    assert created["max_retries"] == 0
    assert providers.MODEL_LIST_TIMEOUT_SECONDS == cli.MODEL_DISCOVERY_TIMEOUT_SECONDS
    assert "enter the model name manually" in capsys.readouterr().out


def test_release_gate_uses_the_resolved_windows_launcher(monkeypatch):
    """Windows command shims such as npm.cmd must be passed to CreateProcess
    by their resolved path instead of the extensionless command name."""
    release_check = runpy.run_path(str(RELEASE_CHECK))
    npm = r"C:\Program Files\nodejs\npm.cmd"
    monkeypatch.setattr(release_check["shutil"], "which", lambda _name: npm)

    assert release_check["_required_executable"]("npm", "missing") == npm


def test_unusable_native_argv_falls_back_to_readonly_docker(
        tmp_path, monkeypatch):
    """Every sandboxed command path shares the safe pre-flight fallback."""
    monkeypatch.setenv("AGENT8088_CONFIG", str(tmp_path / "missing-config.txt"))
    monkeypatch.setenv("AGENT8088_HOME", str(tmp_path))
    from agent8088 import engine

    preflight = "CreateProcessWithLogonW(srt-sandbox): Access is denied."
    calls = []
    monkeypatch.setattr(engine, "_native_sandbox_broken", False)
    monkeypatch.setattr(engine, "_resolve_sandbox_backend", lambda: "native")
    monkeypatch.setattr(engine, "_native_sandbox_argv", lambda: ["node", "srt.js"])
    monkeypatch.setattr(engine, "_write_sandbox_settings", lambda **_kwargs: "settings.json")
    monkeypatch.setattr(engine, "_agent_data_dir", lambda: engine.PROJECT_ROOT)
    monkeypatch.setattr(engine, "_docker_available", lambda: True)
    monkeypatch.setattr(
        engine, "_exec_process",
        lambda *_args, **_kwargs: calls.append("native") or preflight,
    )
    monkeypatch.setattr(
        engine, "_exec_docker_command",
        lambda *args, **kwargs: calls.append(("docker", args, kwargs)) or "ran in docker",
    )

    assert engine._exec_sandbox_argv(["git", "status"]) == "ran in docker"
    assert calls[0] == "native"
    assert calls[1][0] == "docker"
    assert calls[1][2]["image"] == engine.GIT_DOCKER_IMAGE
    assert calls[1][2]["workspace"] == engine.PROJECT_ROOT
    assert calls[1][2]["readonly"] is True


def test_status_reports_docker_after_native_preflight_failure(
        tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT8088_CONFIG", str(tmp_path / "missing-config.txt"))
    monkeypatch.setenv("AGENT8088_HOME", str(tmp_path))
    from agent8088 import engine

    monkeypatch.setattr(engine, "SANDBOX_BACKEND", "auto")
    monkeypatch.setattr(engine, "_native_sandbox_argv", lambda: ["node", "srt.js"])
    monkeypatch.setattr(engine, "_docker_available", lambda: True)
    monkeypatch.setattr(engine, "_native_sandbox_broken", True)

    assert engine.sandbox_status()["resolved"] == "docker"


def test_windows_docker_probe_prefers_the_executable_over_the_unix_shim(
        tmp_path, monkeypatch):
    """Python 3.12.0 can return Docker Desktop's extensionless shell script.
    CreateProcess rejects it with WinError 193 even while Docker is running."""
    monkeypatch.setenv("AGENT8088_CONFIG", str(tmp_path / "missing-config.txt"))
    monkeypatch.setenv("AGENT8088_HOME", str(tmp_path))
    from agent8088 import engine

    unix_shim = r"C:\Program Files\Docker\Docker\resources\bin\docker"
    windows_exe = unix_shim + ".exe"
    seen = []

    def which(name):
        return {"docker": unix_shim, "docker.exe": windows_exe}.get(name)

    monkeypatch.setattr(engine.sys, "platform", "win32")
    monkeypatch.setattr(engine.shutil, "which", which)
    monkeypatch.setattr(
        engine.subprocess, "run",
        lambda argv, **_kwargs: seen.append(argv) or SimpleNamespace(returncode=0),
    )

    assert engine._docker_available() is True
    assert seen == [[windows_exe, "info"]]


def test_native_verifier_finds_the_installed_windows_runtime(tmp_path, monkeypatch):
    """The release gate must use the same data directory as the engine."""
    runtime = (tmp_path / "agent8088" / "runtime" / "node_modules"
               / "@anthropic-ai" / "sandbox-runtime" / "dist" / "cli.js")
    runtime.parent.mkdir(parents=True)
    runtime.touch()
    verifier = runpy.run_path(
        str(INSTALLER.parent / "scripts" / "verify_native_sandbox.py"))
    monkeypatch.delenv("AGENT8088_HOME", raising=False)
    monkeypatch.delenv("AGENT8088_SRT", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(
        verifier["shutil"], "which",
        lambda name: "node.exe" if name == "node" else None,
    )

    assert verifier["_runtime_argv"]() == ["node.exe", str(runtime)]


def test_windows_native_runtime_override_removes_command_line_quotes(
        tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT8088_CONFIG", str(tmp_path / "missing-config.txt"))
    monkeypatch.setenv("AGENT8088_HOME", str(tmp_path))
    from agent8088 import engine

    argv = [r"C:\Program Files\nodejs\node.exe", r"C:\runtime\cli.js"]
    monkeypatch.setattr(engine.sys, "platform", "win32")
    monkeypatch.setenv("AGENT8088_SRT", subprocess.list2cmdline(argv))

    assert engine._native_sandbox_argv() == argv


def test_configured_working_directory_is_used_outside_the_launch_directory(
        tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT8088_CONFIG", str(tmp_path / "missing-config.txt"))
    monkeypatch.setenv("AGENT8088_HOME", str(tmp_path))
    from agent8088 import engine

    launch = tmp_path / "launch"
    workspace = tmp_path / "workspace"
    launch.mkdir()
    workspace.mkdir()

    assert engine._configured_project_root(
        {"allowed_paths": str(workspace)}, launch
    ) == workspace.resolve()


def test_an_allowed_launch_subdirectory_remains_the_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT8088_CONFIG", str(tmp_path / "missing-config.txt"))
    monkeypatch.setenv("AGENT8088_HOME", str(tmp_path))
    from agent8088 import engine

    workspace = tmp_path / "workspace"
    launch = workspace / "package"
    launch.mkdir(parents=True)

    assert engine._configured_project_root(
        {"allowed_paths": str(workspace)}, launch
    ) == launch.resolve()


def test_installers_persist_the_selected_workspace_as_project_root():
    windows = INSTALLER.read_text(encoding="utf-8")
    unix = (INSTALLER.parent / "install.sh").read_text(encoding="utf-8")

    assert '"project_root=$projectRoot"' in windows
    assert 'echo "project_root=$project_root"' in unix


def test_cli_setup_persists_the_selected_workspace_as_project_root(
        tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT8088_CONFIG", str(tmp_path / "config.txt"))
    monkeypatch.setenv("AGENT8088_HOME", str(tmp_path))
    from agent8088 import cli, providers

    config = tmp_path / "config.txt"
    config.write_text(
        "allowed_paths=.\ndefault_provider=ollama\n",
        encoding="utf-8",
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    choices = iter(["ollama", "test-model", "None (disable web search)"])
    saved = {}

    monkeypatch.setattr(cli, "_prompt_workspace_paths", lambda _current: str(workspace))
    monkeypatch.setattr(cli, "_choice_prompt", lambda *_args, **_kwargs: next(choices))
    monkeypatch.setattr(cli, "_custom_prompt", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(cli, "_backfill_memory_key", lambda content, _set_line: content)
    monkeypatch.setattr(providers, "list_models", lambda *_args, **_kwargs: ["test-model"])
    monkeypatch.setattr(
        cli, "_write_private_text",
        lambda path, content: saved.update(path=path, content=content),
    )

    cli._run_setup(config_path=config)

    assert saved["path"] == config
    assert f"allowed_paths={workspace}" in saved["content"]
    assert f"project_root={workspace}" in saved["content"]


def test_installer_keeps_the_documented_docker_fallback():
    installer = INSTALLER.read_text(encoding="utf-8")

    assert "Docker will be used automatically when Docker Desktop is running" in installer
    assert "Sandbox:  Docker fallback is automatic when available" in installer


def test_a_stage_that_reads_stdin_fails_instead_of_hanging(tmp_path):
    """Redirecting from an empty file, not NUL: a stage that reads gets EOF and
    stops, rather than blocking forever on input nobody knows to type."""
    reader = tmp_path / "reads_stdin.py"
    reader.write_text(
        "import sys\n"
        "data = sys.stdin.read()\n"
        "sys.exit(0 if data == '' else 1)\n",
        encoding="ascii",
    )

    out = _powershell(
        'function Test-ProgressAnimated { $true }\n'
        f'$code = Invoke-WithProgress -Label "reader" -FilePath "{sys.executable}" '
        f'-ArgumentList @("{str(reader).replace(chr(92), chr(92) * 2)}")\n'
        'Write-Output "EXIT=$code"'
    )

    assert out.splitlines()[-1] == "EXIT=0", "stdin was not an immediate EOF"


def test_a_missing_executable_does_not_abort_the_install():
    """These stages are optional; the progress display must never be fatal."""
    out = _powershell(
        'function Test-ProgressAnimated { $true }\n'
        '$code = Invoke-WithProgress -Label "test" -FilePath "C:\\nope\\missing.exe" '
        '-ArgumentList @("x")\n'
        'Write-Output "EXIT=$code"'
    )

    assert out.splitlines()[-1] != "EXIT=0"


def test_the_plain_fallback_runs_the_command_too():
    """Redirected output (CI, a log file) must still install, just without a bar."""
    out = _powershell(
        'function Test-ProgressAnimated { $false }\n'
        f'$code = Invoke-WithProgress -Label "test" -FilePath "{sys.executable}" '
        '-ArgumentList @("-c", "import sys; sys.exit(0)")\n'
        'Write-Output "EXIT=$code"'
    )

    assert out.splitlines()[-1] == "EXIT=0"


# ---------------------------------------------------------------------------
# the stages are actually wired to it
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# erasing the animated line
#
# The line is erased by overwriting it with spaces, so the erase has to be as
# wide as the widest line drawn. A fixed 78 was too narrow for the gateway
# stage, whose line came to 88 characters -- the 10 past the end were never
# cleared and "ram)    2s", the tail of "...Telegram)    2s", was left stranded
# on the completed [OK] line above it.
# ---------------------------------------------------------------------------
LONG_LABEL = "Gateway adapters (Slack, Discord, WhatsApp, Telegram)"


def _rendered_width(label, width, suffix="   2s"):
    """Measured inside PowerShell: the indent and padding are the point here,
    and this module's helper strips surrounding whitespace off stdout."""
    bar = "[" + "." * 24 + "]"
    return int(_powershell(
        f'Write-Output (Format-ProgressLine -Bar "{bar}" -Label "{label}" '
        f'-Suffix "{suffix}" -Width {width}).Length'
    ))


def test_a_long_label_cannot_overrun_the_erase_width():
    """The exact line that stranded "ram)    2s" on screen."""
    assert _rendered_width(LONG_LABEL, 78) == 78


def test_every_label_renders_to_exactly_the_erase_width():
    """Short lines must be padded, or the tail of a previous line survives."""
    for label in ("x", "Keyless web search backend (ddgs)", LONG_LABEL, "y" * 200):
        assert _rendered_width(label, 78) == 78, f"{label[:20]!r} broke the width"


def test_the_line_never_exceeds_the_window_and_wraps():
    """A wrapped line cannot be erased: \\r only returns to the last screen row."""
    for width in (40, 60, 78, 100):
        assert _rendered_width(LONG_LABEL, width) == width


def test_the_width_stays_within_the_console():
    """Reported width must leave the last column free; some terminals wrap on it."""
    reported = int(_powershell("Write-Output (Get-ProgressLineWidth)"))
    assert 24 <= reported <= 100


def test_the_erase_is_driven_by_the_same_width_as_the_render():
    """Two independent constants are exactly how the residue appeared: the
    render grew past a hardcoded erase and the overflow stayed on screen."""
    installer = INSTALLER.read_text(encoding="utf-8")

    assert '(" " * $width)' in installer, "erase no longer tracks the render width"
    for hardcoded in ('.PadRight(78)', '(" " * 78)'):
        assert hardcoded not in installer, f"fixed erase width is back: {hardcoded}"


def test_the_shipped_labels_fit_an_eighty_column_console():
    """Truncating mid-word is legible but looks broken; the labels should fit."""
    installer = INSTALLER.read_text(encoding="utf-8")
    labels = re.findall(r'Invoke-WithProgress -Label "([^"]+)"', installer)

    assert labels, "no progress stages found"
    for label in labels:
        # 2 indent + 26 bar + 2 spaces + suffix of 5 ("  60%" / " 120s")
        assert len(label) + 35 <= 78, f"{label!r} overruns an 80-column console"


def test_the_long_stages_no_longer_swallow_their_output():
    """A helper nothing calls would leave the console just as silent."""
    installer = INSTALLER.read_text(encoding="utf-8")
    for stage in ("Chromium", "gateway", "search", "browser"):
        assert f"[{stage}]" in installer or stage in installer

    for silenced in ("playwright install chromium 2>&1 | Out-Null",
                     '-e "$InstallDir[gateway]" 2>&1 | Out-Null',
                     '-e "$InstallDir[browser]" 2>&1 | Out-Null'):
        assert silenced not in installer, f"still discarding output: {silenced}"

    assert installer.count("Invoke-WithProgress -Label") >= 6
