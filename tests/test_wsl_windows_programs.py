"""A command that starts a Windows program runs in the Windows sandbox while the WSL sandbox is active.

The WSL sandbox runs Linux only: interop is off on purpose, so a Linux command cannot start a Windows program with
the user's full rights. A task that needs .NET, MSBuild, PowerShell or a Windows executable would then fail in bash
with "command not found". Such a command goes to the Windows ACL runner instead (writes confined to the project; the
network is not filtered there, and the result says so), with any /mnt/<drive> path turned back into a Windows path.
Everything else stays in WSL.
"""
import sys
from pathlib import Path

import pytest

from agent8088 import wsl_sandbox as W


@pytest.mark.parametrize("command, program", [
    ("dotnet build", "dotnet"),
    ("cd app && msbuild App.sln", "msbuild"),
    ("powershell -File build.ps1", "powershell"),
    ("pwsh -c Get-ChildItem", "pwsh"),
    ("cmd /c dir", "cmd"),
    ("./tools/build.exe --release", "build.exe"),
    ('"C:\\Program Files\\Tool\\tool.exe" run', "tool.exe"),
    ("setup.bat", "setup.bat"),
    ("scripts\\make.cmd all", "make.cmd"),
    (".\\deploy.ps1", "deploy.ps1"),
    ("FOO=1 nuget restore", "nuget"),
    ("echo hi; robocopy a b", "robocopy"),
])
def test_windows_programs_are_recognised(engine, command, program):
    assert engine._windows_program(command) == program


@pytest.mark.parametrize("command", [
    "python3 app.py", "npm run build", "ls -la", "node index.js", "echo powershell is a word here",
    "cat build.exe.log", "git status", "grep -r exe .", "dir", "",
])
def test_ordinary_commands_stay_in_wsl(engine, command):
    assert engine._windows_program(command) == ""


def test_mnt_paths_go_back_to_windows_paths():
    assert W.command_to_windows("dotnet build /mnt/e/proj/App.csproj -o /mnt/e/proj/out") == \
        r"dotnet build E:\proj\App.csproj -o E:\proj\out"
    assert W.command_to_windows('powershell -File "/mnt/c/Users/First Last/x.ps1"') == \
        r'powershell -File "C:\Users\First Last\x.ps1"'
    assert W.command_to_windows("echo /mnt/e") == "echo E:\\"
    assert W.command_to_windows("ls /mnt/wslg/x /home/agent") == "ls /mnt/wslg/x /home/agent"


@pytest.fixture
def routed(engine, monkeypatch, tmp_path):
    """The WSL backend active, with the Windows runner installed; records which backend ran what."""
    ran = []
    monkeypatch.setattr(engine, "_resolve_sandbox_backend", lambda: "wsl")
    monkeypatch.setattr(engine, "_native_sandbox_missing_requirements", lambda: [])
    monkeypatch.setattr(engine, "_native_sandbox_broken", False)
    monkeypatch.setattr(engine, "_shell_workspace_root", tmp_path)
    monkeypatch.setattr(engine, "_sandbox_readonly", False)
    monkeypatch.setattr(engine, "ARTIFACTS_ROOT", tmp_path / "artifacts")
    monkeypatch.setattr(engine, "_interactive_session", lambda: False)

    def fake(backend, command, timeout, python_code, image, workspace):
        ran.append((backend, command))
        return "ok"
    monkeypatch.setattr(engine, "_run_in_sandbox_backend", fake)
    return ran


def test_a_windows_program_runs_in_the_windows_sandbox(engine, routed):
    out = engine._run_sandbox_command("dotnet build /mnt/e/proj/App.csproj")
    assert routed == [("native", r"dotnet build E:\proj\App.csproj")]
    assert "Windows sandbox" in out and "dotnet" in out and "not filtered" in out


def test_everything_else_stays_in_wsl(engine, routed):
    out = engine._run_sandbox_command("npm run build")
    assert routed == [("wsl", "npm run build")]
    assert out == "ok"


def test_python_snippets_are_never_routed(engine, routed):
    engine._run_sandbox_command("import os; os.system('dotnet build')", python_code=True)
    assert routed[0][0] == "wsl"


def test_without_the_windows_runner_it_is_refused_with_the_reason(engine, routed, monkeypatch):
    monkeypatch.setattr(engine, "_native_sandbox_missing_requirements", lambda: ["sandbox-runtime"])
    out = engine._run_sandbox_command("dotnet build")
    assert routed == []
    assert "dotnet is a Windows program" in out and "Nothing was run" in out


def test_other_backends_are_left_alone(engine, routed, monkeypatch):
    monkeypatch.setattr(engine, "_resolve_sandbox_backend", lambda: "docker")
    engine._run_sandbox_command("dotnet build")
    assert routed == [("docker", "dotnet build")]


def test_the_shell_description_says_where_windows_programs_run(engine, monkeypatch):
    monkeypatch.setattr(engine.sys, "platform", "win32")
    monkeypatch.setattr(engine, "_wsl_shell_expected", lambda: True)
    monkeypatch.setattr(engine, "_environment_probe", lambda cwd: "holds a project")
    monkeypatch.setattr(engine, "_SHELL_GROUNDING", {"cwd": None, "suffix": ""})
    specs = {"execute_shell": {"description": "Run a command."}}
    engine._ground_execute_shell_description(specs)
    text = specs["execute_shell"]["description"]
    assert "Windows sandbox" in text and "cannot run in it" not in text
