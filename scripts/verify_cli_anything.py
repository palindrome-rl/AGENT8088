"""Live end-to-end verification for Agent8088's CLI-Anything integration.

This intentionally performs network downloads. It creates an isolated temporary
runtime, exercises the integration through ``engine.run_tool``, and removes the
runtime after a successful run.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path


def main() -> int:
    test_root = Path(tempfile.mkdtemp(prefix="agent8088-cli-anything-live-"))
    os.environ["AGENT8088_CLI_ANYTHING_HOME"] = str(test_root / "integration")

    from agent8088 import engine

    work = test_root / "workspace"
    work.mkdir()
    artifact = work / "gimp-project.json"
    engine.PERMISSION_MODE = "full-auto"
    checks: list[tuple[str, int]] = []

    def call(name: str, args: dict | None = None, contains: str | None = None) -> str:
        result = str(engine.run_tool(name, args or {}))
        if "CLI-Anything exited with status" in result:
            raise RuntimeError(f"{name} failed:\n{result}")
        if contains and contains.lower() not in result.lower():
            raise RuntimeError(f"{name} did not contain {contains!r}:\n{result}")
        checks.append((name, len(result.encode("utf-8"))))
        return result

    try:
        before = json.loads(call("cli_anything_status"))
        if before["available"]:
            raise RuntimeError("Disposable CLI-Anything runtime was unexpectedly preinstalled.")
        call("cli_anything_setup", contains="CLI-Anything is ready")
        after = json.loads(call("cli_anything_status"))
        if not after["available"] or after["version"] != "0.4.1":
            raise RuntimeError(f"Unexpected CLI-Hub state: {after}")

        call("cli_anything_list", contains="gimp")
        call("cli_anything_search", {"query": "gimp"}, contains="gimp")
        call("cli_anything_info", {"name": "gimp"}, contains="cli-anything-gimp")
        call("cli_anything_install", {"name": "gimp"}, contains="Installed gimp")
        skill = call("cli_anything_skill", {"name": "gimp"}, contains="cli-anything-gimp")
        if "--json" not in skill:
            raise RuntimeError("Installed GIMP skill does not document JSON usage.")

        call(
            "cli_anything_run",
            {"name": "gimp", "arguments": ["--help"], "cwd": str(work)},
            contains="project",
        )
        call(
            "cli_anything_run",
            {
                "name": "gimp",
                "arguments": [
                    "--json", "project", "new", "--width", "32", "--height", "32",
                    "--name", "Agent8088 E2E", "--output", str(artifact),
                ],
                "cwd": str(work),
            },
            contains="Agent8088 E2E",
        )
        payload = json.loads(artifact.read_text(encoding="utf-8"))
        canvas = payload.get("canvas", {})
        if canvas.get("width") != 32 or canvas.get("height") != 32:
            raise RuntimeError(f"Artifact has incorrect canvas data: {canvas}")

        call("cli_anything_update", {"name": "gimp"}, contains="Updated gimp")
        call("cli_anything_uninstall", {"name": "gimp"}, contains="Uninstalled gimp")
        try:
            engine.cli_anything.run(engine.CONFIG_PATH, "gimp", ["--help"], work)
        except RuntimeError as exc:
            if "not installed" not in str(exc):
                raise
        else:
            raise RuntimeError("The uninstalled GIMP harness remained runnable.")

        print(f"Verified artifact: {artifact} ({artifact.stat().st_size} bytes)")
        for name, output_bytes in checks:
            print(f"PASS {name} output_bytes={output_bytes}")
        print(f"PASS all {len(checks)} live CLI-Anything operations")
        return 0
    finally:
        shutil.rmtree(test_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
