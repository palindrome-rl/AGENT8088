#!/usr/bin/env python3
"""Strict local publishing gate; every command must pass for a public release."""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
BRIDGE = ROOT / "src" / "agent8088" / "gateway" / "platforms" / "whatsapp_bridge"


def _run(*command: str, env: dict | None = None) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=ROOT, env=env, check=True)


def _required_executable(name: str, error: str) -> str:
    if sys.platform == "win32" and not Path(name).suffix:
        for suffix in (".exe", ".cmd", ".bat", ".com"):
            executable = shutil.which(name + suffix)
            if executable:
                return executable
    executable = shutil.which(name)
    if not executable:
        raise SystemExit(error)
    return executable


def main() -> None:
    uv = _required_executable("uv", "FAIL: uv is required for the release gate")
    npm = _required_executable(
        "npm", "FAIL: npm is required to audit the bundled WhatsApp bridge")

    isolated = dict(os.environ)
    with tempfile.TemporaryDirectory(prefix="agent8088-release-home-") as home:
        isolated.update({"HOME": home, "AGENT8088_HOME": home,
                         "AGENT8088_CONFIG": str(Path(home) / "config.txt"),
                         "PYTHONUTF8": "1"})
        _run(uv, "lock", "--check")
        _run(uv, "sync", "--locked", "--extra", "dev", "--extra", "gateway")
        _run(uv, "run", "--extra", "dev", "--extra", "gateway", "pytest", "-q", env=isolated)
        _run(uv, "run", "--extra", "dev", "ruff", "check", "--select=E9,F", "src", "tests", "scripts")
        _run(uv, "run", "python", "scripts/check_duplicate_defs.py", env=isolated)
        _run(uv, "run", "python", "scripts/check_requirements_sync.py", env=isolated)
        _run(uv, "run", "--extra", "dev", "pip-audit")
        _run(npm, "ci", "--ignore-scripts", "--prefix", str(BRIDGE))
        _run(npm, "audit", "--omit=dev", "--prefix", str(BRIDGE))
        # The verifier discovers the installed native runtime, then isolates its
        # own child process and temporary home before executing sandbox probes.
        _run(uv, "run", "python", "scripts/verify_native_sandbox.py")
        _run(uv, "run", "--extra", "dev", "--extra", "gateway",
             "python", "scripts/verify_features.py", env=isolated)

        with tempfile.TemporaryDirectory(prefix="agent8088-release-build-") as build_dir:
            build = Path(build_dir)
            _run(uv, "build", "--out-dir", str(build / "dist"))
            wheels = list((build / "dist").glob("*.whl"))
            if len(wheels) != 1:
                raise SystemExit("FAIL: expected exactly one wheel from uv build")
            venv = build / "venv"
            _run(uv, "venv", str(venv))
            python = venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
            _run(uv, "pip", "install", "--python", str(python), f"{wheels[0]}[gateway]")
            _run(str(python), "-m", "agent8088.cli", "--version", env=isolated)

    print("PASS: release gate completed")


if __name__ == "__main__":
    main()
