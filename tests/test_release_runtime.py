import runpy
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_release_gate_uses_the_resolved_command_launcher(monkeypatch):
    release_check = runpy.run_path(str(ROOT / "scripts" / "release_check.py"))
    unix_shim = r"C:\Program Files\nodejs\npm"
    npm = r"C:\Program Files\nodejs\npm.cmd"
    monkeypatch.setattr(release_check["sys"], "platform", "win32")
    monkeypatch.setattr(
        release_check["shutil"], "which",
        lambda name: {"npm": unix_shim, "npm.cmd": npm}.get(name),
    )

    assert release_check["_required_executable"]("npm", "missing") == npm


def test_native_verifier_finds_the_windows_install_location(tmp_path, monkeypatch):
    runtime = (tmp_path / "agent8088" / "runtime" / "node_modules"
               / "@anthropic-ai" / "sandbox-runtime" / "dist" / "cli.js")
    runtime.parent.mkdir(parents=True)
    runtime.touch()
    verifier = runpy.run_path(str(ROOT / "scripts" / "verify_native_sandbox.py"))
    monkeypatch.delenv("AGENT8088_HOME", raising=False)
    monkeypatch.delenv("AGENT8088_SRT", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(verifier["sys"], "platform", "win32")
    monkeypatch.setattr(
        verifier["shutil"], "which",
        lambda name: "node.exe" if name == "node" else None,
    )

    assert verifier["_runtime_argv"]() == ["node.exe", str(runtime)]
