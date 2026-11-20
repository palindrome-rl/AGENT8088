"""Grader for npm-esbuild-bundle.

    python grade.py <workspace> --json <report.json>

Rebuilds with the project's own `npm run build` (after removing dist/), then runs
dist/app.js with node_modules moved aside, so the bundle passes only if dayjs
really is inside it. The output must match expected_output.txt.
"""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _run(command, cwd, timeout):
    # npm is npm.cmd on Windows, which needs the shell to start.
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                          shell=sys.platform == "win32", encoding="utf-8", errors="replace", check=False)


def grade(workspace: Path) -> dict:
    checks = {}
    if shutil.which("node") is None or shutil.which("npm") is None:
        return {"error": "node and npm are needed to grade this task"}
    try:
        package = json.loads((workspace / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        package = {}
    checks["dayjs is a dependency"] = "dayjs" in (package.get("dependencies") or {})
    checks["esbuild is a dev dependency"] = "esbuild" in (package.get("devDependencies") or {})
    checks["esbuild is installed"] = (workspace / "node_modules" / "esbuild" / "package.json").is_file()
    build = str((package.get("scripts") or {}).get("build") or "")
    checks["the build script uses esbuild"] = "esbuild" in build

    output, built = "", False
    if build:
        shutil.rmtree(workspace / "dist", ignore_errors=True)
        built = _run("npm run build", workspace, 300).returncode == 0 and (workspace / "dist" / "app.js").is_file()
    checks["npm run build makes dist/app.js"] = built

    ran = False
    if built:
        modules, aside = workspace / "node_modules", workspace / "node_modules.grader-aside"
        moved = modules.is_dir()
        if moved:
            modules.rename(aside)
        try:
            done = _run("node dist/app.js", workspace, 60)
            ran, output = done.returncode == 0, done.stdout
        finally:
            if moved:
                aside.rename(modules)
    checks["dist/app.js runs without node_modules"] = ran
    expected = (HERE / "expected_output.txt").read_text(encoding="utf-8")
    checks["the output is right"] = ran and output.replace("\r\n", "\n").strip() == expected.strip()

    score = sum(checks.values()) / len(checks)
    return {"passed": all(checks.values()), "score": round(score, 3), "checks": checks}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("workspace")
    parser.add_argument("--json", required=True)
    args = parser.parse_args()
    report = grade(Path(args.workspace))
    Path(args.json).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    sys.exit(main())
