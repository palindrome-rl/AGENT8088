"""Opt-in real OpenCascade/cadgen integration coverage.

These drive the vendored upstream text-to-cad skill's own CLIs exactly the way
the model does -- the CAD venv's interpreter, a script under the skill's
`scripts/`, cwd set to the workspace, bare relative filenames. If the shape
here stops working, the model's path is broken too.
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from agent8088 import cad

pytestmark = pytest.mark.skipif(
    os.environ.get("AGENT8088_RUN_CAD_E2E") != "1",
    reason="set AGENT8088_RUN_CAD_E2E=1 with an installed isolated CAD runtime",
)

BOX = (
    "from build123d import Box\n\n"
    "PARAMS = {}\n\n\n"
    "def gen_step():\n"
    "    return Box(2, 3, 5)\n"
)


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "box.step.py").write_text(BOX, encoding="utf-8")
    return tmp_path


def _run(script, *args, cwd):
    """Invoke a vendored skill script the way _cad_runtime_instruction tells the model to."""
    from agent8088 import engine

    command = [cad.cad_runtime_python(), str(engine._cad_skill_scripts_dir() / script), *args]
    completed = subprocess.run(
        command, capture_output=True, text=True, timeout=300, check=False, cwd=str(cwd)
    )
    return completed


def test_gen_writes_a_step_beside_its_generator(workspace):
    completed = _run("gen", "box.step.py", "--write", "--json", cwd=workspace)
    assert completed.returncode == 0, completed.stderr
    assert (workspace / "box.step").is_file()


def test_inspect_validate_reports_a_sound_solid(workspace):
    assert _run("gen", "box.step.py", "--write", "--json", cwd=workspace).returncode == 0
    completed = _run("inspect", "validate", "box.step.py", cwd=workspace)
    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report["ok"] is True
    assert report["failureCount"] == 0


def test_inspect_refs_needs_the_workspace_as_cwd(workspace):
    """The invocation shape is load-bearing, not incidental.

    `scripts/inspect refs` resolves its target relative to cwd, so the absolute
    path a model might reach for fails. This pins the contract the runtime
    instruction states, so a future edit to that wording cannot drift from it.
    """
    assert _run("gen", "box.step.py", "--write", "--json", cwd=workspace).returncode == 0

    good = _run("inspect", "refs", "box.step", "--facts", cwd=workspace)
    assert good.returncode == 0, good.stderr
    assert json.loads(good.stdout)["ok"] is True

    bad = _run("inspect", "refs", str(workspace / "box.step"), "--facts", cwd=workspace.parent)
    assert bad.returncode != 0 or json.loads(bad.stdout)["ok"] is False


def test_generated_step_reopens_with_the_expected_geometry(workspace):
    assert _run("gen", "box.step.py", "--write", "--json", cwd=workspace).returncode == 0
    from build123d import import_step

    reopened = import_step(workspace / "box.step")
    assert len(reopened.solids()) == 1
    assert abs(reopened.volume - 30) < 1e-6


def test_extract_info_summarises_a_real_step(workspace):
    assert _run("gen", "box.step.py", "--write", "--json", cwd=workspace).returncode == 0
    info = cad.extract_info(workspace / "box.step")
    assert info, "extract_info must summarise a real STEP file"


def test_generation_failure_is_reported_not_silently_written(tmp_path):
    (tmp_path / "broken.step.py").write_text(
        "PARAMS = {}\n\n\ndef gen_step():\n    raise ValueError('nope')\n", encoding="utf-8"
    )
    completed = _run("gen", "broken.step.py", "--write", "--json", cwd=tmp_path)
    assert completed.returncode != 0
    assert not (tmp_path / "broken.step").exists()
