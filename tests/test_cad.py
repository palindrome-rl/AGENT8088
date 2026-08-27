"""Unit coverage for the isolated build123d + text-to-cad CAD boundary."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from agent8088 import cad, cad_worker


def _ok_result(**extra):
    return {
        "ok": True,
        "solid_count": 1,
        "volume": 15000.0,
        "bounding_box": {"size": [50.0, 30.0, 10.0]},
        "validity": {"ok": True, "reasons": []},
        **extra,
    }


def test_runtime_status_reports_a_missing_interpreter(monkeypatch, tmp_path):
    missing = tmp_path / "missing-python"
    monkeypatch.setenv("AGENT8088_CAD_PYTHON", str(missing))
    status = cad.cad_runtime_status()
    assert status["available"] is False
    assert status["reason"] == "runtime interpreter is missing"


def test_runtime_status_requires_the_exact_pinned_versions(monkeypatch, tmp_path):
    python = tmp_path / "python.exe"
    python.write_bytes(b"x")
    monkeypatch.setenv("AGENT8088_CAD_PYTHON", str(python))
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 0, "0.11.0|0.4.26\n", ""),
    )
    assert cad.cad_runtime_status()["available"] is False


def test_missing_runtime_is_actionable(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT8088_CAD_PYTHON", str(tmp_path / "missing"))
    src = tmp_path / "part.step"
    src.write_bytes(b"step")
    result = cad.convert_cad(src, "stl")
    assert "not installed" in result
    assert "Re-run the Agent8088 installer" in result
    assert "build123d" in result and "text-to-cad" in result


def test_convert_rejects_unsupported_same_and_lossy_mesh_targets(tmp_path):
    step = tmp_path / "part.step"
    step.write_bytes(b"step")
    assert "Supported targets" in cad.convert_cad(step, "pdf")
    assert "both step" in cad.convert_cad(step, "step")
    mesh = tmp_path / "part.stl"
    mesh.write_bytes(b"mesh")
    assert "trustworthy solid B-rep" in cad.convert_cad(mesh, "step")


def test_convert_requires_source_file(tmp_path):
    assert "does not exist" in cad.convert_cad(tmp_path / "missing.step", "stl")


def test_convert_requires_a_real_output(monkeypatch, tmp_path):
    src = tmp_path / "part.step"
    src.write_bytes(b"step")
    monkeypatch.setattr(cad, "_run_worker", lambda *a, **k: _ok_result())
    assert "was not created" in cad.convert_cad(src, "stl")


def test_convert_success_reports_verified_metrics(monkeypatch, tmp_path):
    src = tmp_path / "part.step"
    src.write_bytes(b"step")

    def worker(request, timeout):
        (tmp_path / "part.stl").write_bytes(b"solid mesh")
        return _ok_result()

    monkeypatch.setattr(cad, "_run_worker", worker)
    result = cad.convert_cad(src, ".STL")
    assert "Converted part.step to part.stl" in result
    assert "Bounding box: 50.000 x 30.000 x 10.000" in result
    assert "Geometry: valid" in result


def test_run_worker_timeout_is_plain_language(monkeypatch, tmp_path):
    python = tmp_path / "python.exe"
    python.write_bytes(b"x")
    monkeypatch.setenv("AGENT8088_CAD_PYTHON", str(python))
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: (_ for _ in ()).throw(subprocess.TimeoutExpired(a[0], 1)),
    )
    result = cad._run_worker({"workspace": str(tmp_path), "action": "inspect"}, 1)
    assert result == {"ok": False, "error": "CAD operation timed out after 1s."}


def test_primitive_input_validation_never_needs_the_runtime(tmp_path):
    assert "Unknown shape" in cad.create_cad_part(tmp_path / "x.step", "torus", "10x5")
    assert "Supported output formats" in cad.create_cad_part(tmp_path / "x.docx", "box", "1x2x3")
    assert "needs 3" in cad.create_cad_part(tmp_path / "x.step", "box", "1x2")
    assert "not a number" in cad.create_cad_part(tmp_path / "x.step", "box", "1xbrokex3")
    assert "inner radius" in cad.create_cad_part(tmp_path / "x.step", "tube", "10x12x20")


def test_primitive_key_value_dimensions_and_artifact_contract(monkeypatch, tmp_path):
    seen = {}

    def worker(request, timeout):
        seen.update(request)
        (tmp_path / "cylinder.step").write_bytes(b"verified step")
        return _ok_result()

    monkeypatch.setattr(cad, "_run_worker", worker)
    result = cad.create_cad_part(
        tmp_path / "cylinder.step", "cylinder", "radius=10,height=50"
    )
    assert seen["dimensions"] == {"radius": 10.0, "height": 50.0}
    assert "Created cylinder.step" in result


def test_generate_requires_step_source_and_parameter_object(tmp_path):
    source = "from build123d import Box\ndef gen_step():\n    return Box(1, 2, 3)"
    assert "requires a .step" in cad.generate_cad_model(tmp_path / "x.stl", source)
    assert "non-empty" in cad.generate_cad_model(tmp_path / "x.step", "")
    assert "JSON object" in cad.generate_cad_model(tmp_path / "x.step", source, "[]")
    assert "Unsupported" in cad.generate_cad_model(tmp_path / "x.step", source, "{}", "dxf")


def test_generate_writes_reproducible_inputs_and_requires_every_artifact(monkeypatch, tmp_path):
    source = "from build123d import Box\ndef gen_step():\n    return Box(PARAMS['x'], 2, 3)"

    def incomplete(request, timeout):
        for key in ("output", "report"):
            Path(request[key]).write_bytes(b"x")
        return _ok_result()

    monkeypatch.setattr(cad, "_run_worker", incomplete)
    result = cad.generate_cad_model(tmp_path / "model.step", source, '{"x": 5}', "step,stl")
    assert "failed verification" in result
    assert (tmp_path / "model.step.py").read_text(encoding="utf-8").startswith(
        "# Parametric build123d model generated by Agent8088."
    )
    assert json.loads((tmp_path / "model.params.json").read_text()) == {"x": 5}


def test_generate_success_returns_complete_bundle(monkeypatch, tmp_path):
    source = "from build123d import Box\ndef gen_step():\n    return Box(1, 2, 3)"

    def complete(request, timeout):
        for key in ("output", "report", "preview"):
            Path(request[key]).write_bytes(b"artifact")
        Path(request["output"]).with_suffix(".stl").write_bytes(b"mesh")
        return _ok_result()

    monkeypatch.setattr(cad, "_run_worker", complete)
    result = cad.generate_cad_model(tmp_path / "model.step", source)
    assert "Generated and verified model.step" in result
    assert "model.preview.png" in result
    assert "model.params.json" in result


def test_validate_requires_report_and_preview(monkeypatch, tmp_path):
    model = tmp_path / "model.step"
    model.write_bytes(b"step")
    monkeypatch.setattr(cad, "_run_worker", lambda *a, **k: _ok_result())
    assert "failed verification" in cad.validate_cad_model(model)


def test_extract_info_falls_through_for_non_cad(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("plain")
    assert cad.extract_info(path) is None


def test_generator_guard_accepts_normal_build123d_source(tmp_path):
    source = tmp_path / "safe.step.py"
    source.write_text(
        "PARAMS = {'x': 2}\n"
        "from build123d import Box\n"
        "def gen_step():\n    return Box(PARAMS['x'], 3, 4)\n",
        encoding="utf-8",
    )
    cad_worker._validate_generator_source(source)


def test_generator_guard_rejects_file_process_dynamic_and_export_escape_hatches(tmp_path):
    cases = {
        "os": "import os\ndef gen_step():\n    return os.system('whoami')",
        "open": "def gen_step():\n    return open('stolen.txt', 'w')",
        "getattr": "from build123d import Box\ndef gen_step():\n    return getattr(Box, '__mro__')",
        "attribute_export": (
            "import build123d\ndef gen_step():\n"
            "    return build123d.export_step(build123d.Box(1,2,3), 'escape.step')"
        ),
        "aliased_export": (
            "from build123d import Box, export_step as send\n"
            "def gen_step():\n    return send(Box(1,2,3), 'escape.step')"
        ),
        "cadgen_runtime": "import cadgen\ndef gen_step():\n    return cadgen.snapshot_cli([])",
        "unbounded_loop": "def gen_step():\n    while True:\n        pass",
    }
    for name, body in cases.items():
        source = tmp_path / f"{name}.step.py"
        source.write_text("PARAMS = {}\n" + body + "\n", encoding="utf-8")
        try:
            cad_worker._validate_generator_source(source)
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe generator was accepted: {name}")


def test_generator_guard_rejects_replacing_injected_parameters(tmp_path):
    source = tmp_path / "params.step.py"
    source.write_text(
        "PARAMS = {'x': 2}\nfrom build123d import Box\n"
        "def gen_step():\n    global PARAMS\n    PARAMS = {}\n    return Box(1,2,3)\n",
        encoding="utf-8",
    )
    try:
        cad_worker._validate_generator_source(source)
    except ValueError as exc:
        assert "PARAMS" in str(exc)
    else:
        raise AssertionError("PARAMS replacement was accepted")


def test_worker_rejects_paths_outside_the_authorized_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    request = {
        "workspace": str(workspace),
        "input": str(tmp_path / "outside.step"),
    }
    try:
        cad_worker._require_workspace_paths(request)
    except ValueError as exc:
        assert "outside" in str(exc)
    else:
        raise AssertionError("out-of-workspace path was accepted")
