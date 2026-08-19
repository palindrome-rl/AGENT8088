"""Windows ACL native sandbox: argv construction, requirement checks, repair hints.

These run on every platform by monkeypatching `engine.sys.platform` to "win32"
rather than requiring actual Windows hardware - the Win32 API behavior itself
(CreateRestrictedToken, ACL grants) can only be proven on real Windows, see
`.claude/plans/2026-08-19_154753-windows-acl-native-sandbox.md` Task 10.
"""
import sys

from agent8088 import engine


def test_windows_shell_command_wraps_cmd_exe(monkeypatch):
    monkeypatch.setenv("SystemRoot", r"C:\Windows")
    argv = engine._native_sandbox_shell_argv('cd /d C:\\ws && set "TMPDIR=C:\\tmp"& echo hi')
    assert argv[0].lower().endswith("cmd.exe")
    assert argv[1:4] == ["/d", "/s", "/c"]
    assert argv[4] == 'cd /d C:\\ws && set "TMPDIR=C:\\tmp"& echo hi'


def test_dsh_runner_path_resolution(tmp_path, monkeypatch):
    fake_home = tmp_path / "agent8088-home"
    monkeypatch.setenv("AGENT8088_HOME", str(fake_home))
    runner = (fake_home / "runtime" / "node_modules" / "@deepseek-ai"
              / "dsh-sandbox-windows-acl" / "lib" / "runner.js")
    runner.parent.mkdir(parents=True)
    runner.write_text("// stub")
    assert engine._dsh_runner_path() == runner


def test_native_sandbox_argv_uses_dsh_runner_on_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(engine.sys, "platform", "win32")
    monkeypatch.delenv("AGENT8088_SRT", raising=False)
    fake_home = tmp_path / "agent8088-home"
    monkeypatch.setenv("AGENT8088_HOME", str(fake_home))
    runner = engine._dsh_runner_path()
    runner.parent.mkdir(parents=True)
    runner.write_text("// stub")
    monkeypatch.setattr(engine, "_which_executable",
                         lambda name: r"C:\node\node.exe" if name == "node" else None)

    argv = engine._native_sandbox_argv()

    assert argv == [r"C:\node\node.exe", str(runner)]


def test_native_sandbox_argv_missing_runner_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(engine.sys, "platform", "win32")
    monkeypatch.delenv("AGENT8088_SRT", raising=False)
    monkeypatch.setenv("AGENT8088_HOME", str(tmp_path / "agent8088-home"))
    monkeypatch.setattr(engine, "_which_executable",
                         lambda name: r"C:\node\node.exe" if name == "node" else None)

    assert engine._native_sandbox_argv() is None


def test_native_sandbox_argv_respects_override_on_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(engine.sys, "platform", "win32")
    monkeypatch.setenv("AGENT8088_SRT", '"C:\\custom\\srt.exe" arg')

    assert engine._native_sandbox_argv() == [r"C:\custom\srt.exe", "arg"]


def test_exec_native_sandbox_builds_dsh_argv_on_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(engine.sys, "platform", "win32")
    monkeypatch.setenv("AGENT8088_HOME", str(tmp_path / "home"))
    runner = engine._dsh_runner_path()
    runner.parent.mkdir(parents=True)
    runner.write_text("// stub")
    monkeypatch.setattr(engine, "_which_executable",
                         lambda name: r"C:\node\node.exe" if name == "node" else None)
    captured = {}

    def fake_exec_process(argv, timeout):
        captured["argv"] = argv
        return "ok"

    monkeypatch.setattr(engine, "_exec_process", fake_exec_process)

    engine._exec_native_sandbox("echo hi", timeout=10, cwd=tmp_path)

    argv = captured["argv"]
    assert "--workspace" in argv
    assert "--mode" in argv
    assert argv[argv.index("--mode") + 1] == "workspace-write"
    assert argv[-1] == "echo hi"  # the wrapped command is the final cmd.exe arg
    assert argv[argv.index("--") + 1].lower().endswith("cmd.exe")


def test_exec_native_sandbox_readonly_uses_read_only_mode_on_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(engine.sys, "platform", "win32")
    monkeypatch.setenv("AGENT8088_HOME", str(tmp_path / "home"))
    runner = engine._dsh_runner_path()
    runner.parent.mkdir(parents=True)
    runner.write_text("// stub")
    monkeypatch.setattr(engine, "_which_executable",
                         lambda name: r"C:\node\node.exe" if name == "node" else None)
    captured = {}
    monkeypatch.setattr(engine, "_exec_process",
                         lambda argv, timeout: captured.setdefault("argv", argv) or "ok")

    engine._exec_native_sandbox("echo hi", timeout=10, cwd=tmp_path, readonly=True)

    argv = captured["argv"]
    assert argv[argv.index("--mode") + 1] == "read-only"


def test_missing_requirements_flags_absent_koffi_addon(tmp_path, monkeypatch):
    monkeypatch.setattr(engine.sys, "platform", "win32")
    monkeypatch.setenv("AGENT8088_HOME", str(tmp_path / "home"))
    runner = engine._dsh_runner_path()
    runner.parent.mkdir(parents=True)
    runner.write_text("// stub")
    monkeypatch.setattr(engine, "_which_executable",
                         lambda name: r"C:\node\node.exe" if name == "node" else None)

    missing = engine._native_sandbox_missing_requirements()

    assert "koffi native addon" in missing


def test_missing_requirements_empty_when_koffi_present(tmp_path, monkeypatch):
    monkeypatch.setattr(engine.sys, "platform", "win32")
    monkeypatch.setenv("AGENT8088_HOME", str(tmp_path / "home"))
    runner = engine._dsh_runner_path()
    runner.parent.mkdir(parents=True)
    runner.write_text("// stub")
    koffi_dir = tmp_path / "home" / "runtime" / "node_modules" / "koffi"
    koffi_dir.mkdir(parents=True)
    monkeypatch.setattr(engine, "_which_executable",
                         lambda name: r"C:\node\node.exe" if name == "node" else None)

    assert engine._native_sandbox_missing_requirements() == []


def test_repair_hint_windows_acl_runner_points_at_sandbox_setup():
    hint = engine._native_sandbox_repair_hint("windows-acl-run: workspace grant failed")
    assert "--sandbox-setup" in hint
    assert "seclogon" not in hint.lower()


def test_repair_hint_access_denied_points_at_sandbox_setup_not_seclogon():
    hint = engine._native_sandbox_repair_hint("Access is denied.")
    assert "--sandbox-setup" in hint
    assert "seclogon" not in hint.lower()
    assert "antivirus" not in hint.lower()


def test_windows_acl_runner_failure_is_recognized_as_preflight():
    assert engine._native_sandbox_unusable("windows-acl-run: bad --workspace argument")
