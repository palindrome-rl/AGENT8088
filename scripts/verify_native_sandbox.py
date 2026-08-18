#!/usr/bin/env python3
"""Fail-fast proof that the installed OS-native sandbox enforces its contract.

Run after ``agent8088 --sandbox-setup`` on every platform that will receive a
release. This deliberately uses the real sandbox runtime; it does not silently
skip when a prerequisite is absent.
"""
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
MARKER = "agent8088-native-sandbox-secret"


def _fail(message: str) -> None:
    raise SystemExit(f"FAIL: {message}")


def _runtime_argv() -> list[str] | None:
    override = os.environ.get("AGENT8088_SRT")
    if override:
        return shlex.split(override, posix=sys.platform != "win32")
    if os.environ.get("AGENT8088_HOME"):
        data_dir = Path(os.environ["AGENT8088_HOME"])
    elif sys.platform == "win32":
        data_dir = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "agent8088"
    else:
        data_dir = Path.home() / ".agent8088"
    cli = data_dir / "runtime" / "node_modules" / "@anthropic-ai" / "sandbox-runtime" / "dist" / "cli.js"
    node = shutil.which("node")
    if node and cli.exists():
        return [node, str(cli)]
    executable = shutil.which("srt")
    return [executable] if executable else None


def _require_prerequisites() -> list[str]:
    runtime = _runtime_argv()
    if not runtime:
        _fail("native sandbox runtime missing; run agent8088 --sandbox-setup first")
    required = {"darwin": ("sandbox-exec", "rg"), "linux": ("bwrap", "socat", "rg")}.get(sys.platform, ())
    missing = [name for name in required if not shutil.which(name)]
    if missing:
        _fail(f"native sandbox prerequisites missing: {', '.join(missing)}")
    return runtime


def _child(workspace: Path, secret: Path, config: Path) -> None:
    from agent8088 import engine

    if engine._resolve_sandbox_backend() != "native":
        _fail(f"native backend was not selected: {engine.sandbox_status()['detail']}")

    allowed = engine.ARTIFACTS_ROOT / "allowed.txt"
    write_result = engine._exec_sandbox_command(
        f"printf allowed > {shlex.quote(str(allowed))}")
    if not allowed.is_file() or allowed.read_text(encoding="utf-8") != "allowed":
        _fail(f"native sandbox could not write the permitted workspace: {write_result}")

    read_result = engine._exec_sandbox_command(f"cat {shlex.quote(str(secret))}")
    if MARKER in read_result:
        _fail("native sandbox read a protected credential path")
    engine._exec_sandbox_command(f"printf altered > {shlex.quote(str(secret))}")
    if secret.read_text(encoding="utf-8") != MARKER:
        _fail("native sandbox wrote a protected credential path")
    engine._exec_sandbox_command(f"printf altered > {shlex.quote(str(config))}")
    if "sandbox_backend=native" not in config.read_text(encoding="utf-8"):
        _fail("native sandbox wrote its protected configuration")

    network_code = "import socket; socket.create_connection(('example.com', 443), timeout=2); print('A8088' + '_NET_OK')"
    network_result = engine._exec_sandbox_command(
        shlex.join([sys.executable, "-c", network_code]), timeout=6)
    if "A8088_NET_OK" in network_result:
        _fail(f"native sandbox reached the network without an allowlist: {network_result[:1000]}")
    timeout_result = engine._exec_sandbox_command(
        shlex.join([sys.executable, "-c", "import time; time.sleep(30)"]), timeout=1)
    if "timed out" not in timeout_result.lower():
        _fail("native sandbox did not terminate a timed-out command")

    original_runtime = engine._native_sandbox_argv
    try:
        engine._native_sandbox_argv = lambda: None
        missing_result = engine._exec_sandbox_command("echo must-not-run")
    finally:
        engine._native_sandbox_argv = original_runtime
    if "ESCALATION_REQUEST" not in missing_result:
        _fail("missing native sandbox fell back to local execution")
    print("PASS: native sandbox enforced workspace, credential, network, timeout, and fallback boundaries")


def main() -> None:
    if len(sys.argv) == 4 and sys.argv[1] == "--child":
        _child(Path(sys.argv[2]), Path(sys.argv[3]), Path(os.environ["AGENT8088_CONFIG"]))
        return

    runtime = _require_prerequisites()
    with tempfile.TemporaryDirectory(prefix="agent8088-native-sandbox-") as temporary:
        root = Path(temporary)
        workspace = root / "workspace"
        home = root / "home"
        secret = home / ".ssh" / "release-secret"
        workspace.mkdir()
        secret.parent.mkdir(parents=True)
        secret.write_text(MARKER, encoding="utf-8")
        config = root / "config.txt"
        config.write_text(
            f"project_root={workspace}\nshell_cwd={workspace}\nallowed_paths=.\nsandbox_backend=native\n",
            encoding="utf-8",
        )
        runtime_command = (subprocess.list2cmdline(runtime) if sys.platform == "win32"
                           else shlex.join(runtime))
        env = dict(os.environ, AGENT8088_CONFIG=str(config), AGENT8088_HOME=str(home),
                   AGENT8088_SANDBOX="native", AGENT8088_SRT=runtime_command, HOME=str(home))
        source = str(ROOT / "src")
        env["PYTHONPATH"] = source + os.pathsep + env.get("PYTHONPATH", "")
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--child", str(workspace), str(secret)],
            env=env, text=True, capture_output=True,
        )
        if result.returncode:
            _fail(result.stdout.strip() or result.stderr.strip() or "native sandbox verification failed")
        print(result.stdout.strip())


if __name__ == "__main__":
    main()
