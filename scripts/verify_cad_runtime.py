#!/usr/bin/env python3
"""Perform a real worker/export/reopen check for the isolated CAD runtime."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    worker = root / "src" / "agent8088" / "cad_worker.py"
    with tempfile.TemporaryDirectory(prefix="agent8088-cad-smoke-") as raw:
        workspace = Path(raw)
        output = workspace / "box.step"
        request = workspace / "request.json"
        request.write_text(
            json.dumps({
                "action": "primitive",
                "shape": "box",
                "dimensions": {"length": 2, "width": 3, "height": 5},
                "output": str(output),
                "workspace": str(workspace),
            }),
            encoding="utf-8",
        )
        completed = subprocess.run(
            [sys.executable, "-I", str(worker), str(request)],
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        if completed.returncode:
            print(completed.stdout, file=sys.stderr)
            print(completed.stderr, file=sys.stderr)
            return completed.returncode
        report = workspace / "box.report.json"
        preview = workspace / "box.preview.png"
        request.write_text(
            json.dumps({
                "action": "validate",
                "input": str(output),
                "report": str(report),
                "preview": str(preview),
                "workspace": str(workspace),
            }),
            encoding="utf-8",
        )
        completed = subprocess.run(
            [sys.executable, "-I", str(worker), str(request)],
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        if completed.returncode:
            print(completed.stdout, file=sys.stderr)
            print(completed.stderr, file=sys.stderr)
            return completed.returncode

        from build123d import import_step

        reopened = import_step(output)
        assert len(reopened.solids()) == 1
        assert abs(reopened.volume - 30) < 1e-6
        assert report.stat().st_size > 0
        assert preview.stat().st_size > 0
        payload = json.loads(report.read_text(encoding="utf-8"))
        assert payload["validity"]["ok"] is True
        print("CAD runtime worker/export/reopen/render round trip: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
