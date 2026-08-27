"""Opt-in real OpenCascade/cadgen/browser integration coverage."""

from __future__ import annotations

import json
import os

import pytest

from agent8088 import cad

pytestmark = pytest.mark.skipif(
    os.environ.get("AGENT8088_RUN_CAD_E2E") != "1",
    reason="set AGENT8088_RUN_CAD_E2E=1 with an installed isolated CAD runtime",
)


def test_parameterized_part_all_formats_and_preview_round_trip(tmp_path):
    output_dir = tmp_path / "CAD output with spaces"
    output_dir.mkdir()
    model = output_dir / "mounting plate.step"
    params = {
        "length": 80,
        "width": 50,
        "height": 8,
        "hole_radius": 3,
        "boss_radius": 10,
        "boss_height": 12,
    }
    source = """from build123d import Align, Box, Cylinder, Pos

def gen_step():
    body = Box(
        PARAMS["length"], PARAMS["width"], PARAMS["height"],
        align=(Align.MIN, Align.MIN, Align.MIN),
    )
    left_hole = Pos(15, 15, -1) * Cylinder(
        PARAMS["hole_radius"], PARAMS["height"] + 2,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    )
    right_hole = Pos(PARAMS["length"] - 15, 15, -1) * Cylinder(
        PARAMS["hole_radius"], PARAMS["height"] + 2,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    )
    body = body - left_hole - right_hole
    boss = Pos(PARAMS["length"] / 2, PARAMS["width"] / 2, PARAMS["height"] - 1) * Cylinder(
        PARAMS["boss_radius"], PARAMS["boss_height"] + 1,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    )
    model = body + boss
    model.label = "mounting_plate"
    return model
"""
    result = cad.generate_cad_model(
        model, source, json.dumps(params), "step,stl,3mf,glb,brep", timeout=900
    )
    assert "Generated and verified" in result, result

    for suffix in (
        ".step", ".stl", ".3mf", ".glb", ".brep", ".step.py",
        ".params.json", ".report.json", ".preview.png",
    ):
        artifact = model.with_suffix(suffix)
        assert artifact.is_file() and artifact.stat().st_size > 0, artifact

    report = json.loads(model.with_suffix(".report.json").read_text(encoding="utf-8"))
    assert report["validity"]["ok"] is True
    assert report["solid_count"] == 1
    assert report["volume"] > 0
    assert report["bounding_box"]["size"] == pytest.approx([80, 50, 20])

    inspection = cad.extract_info(model)
    assert "Geometry: valid" in inspection
    assert "Solids: 1" in inspection
    assert "Validated mounting plate.step" in cad.validate_cad_model(model)
    assert "Mesh bodies: 1" in cad.extract_info(model.with_suffix(".3mf"))
    assert "Mesh bodies: 1" in cad.extract_info(model.with_suffix(".glb"))


def test_generated_source_cannot_write_files(tmp_path):
    model = tmp_path / "unsafe.step"
    source = """from build123d import Box

def gen_step():
    open("escaped.txt", "w").write("not allowed")
    return Box(1, 2, 3)
"""
    result = cad.generate_cad_model(model, source, "{}", "step")
    assert "not allowed" in result
    assert not model.exists()
    assert not (tmp_path / "escaped.txt").exists()
