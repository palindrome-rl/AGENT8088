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


def _response(content):
    return type("R", (), {"choices": [type("C", (), {
        "message": type("M", (), {"content": content})(),
        "finish_reason": "stop",
    })()]})()


def _cad_tool_definitions(engine):
    wanted = {"generate_cad_design", "generate_cad_model", "open_cad_viewer"}
    return [
        item for item in engine.build_tools_def(engine.TOOL_SPECS)
        if item["function"]["name"] in wanted
    ]


def test_agent_loop_structured_design_to_viewer_handoff(engine, tmp_path, monkeypatch):
    """The same outer JSON shape a model emits must reach the real CAD worker."""
    model = tmp_path / "verified angle bracket.step"
    design = {
        "schema_version": 1,
        "name": "verified_angle_bracket",
        "units": "mm",
        "parameters": {"base_x": 60, "base_y": 40, "base_z": 6, "wall_h": 30},
        "components": [
            {
                "name": "base_plate",
                "add": [{"type": "box", "size": ["base_x", "base_y", "base_z"]}],
                "cut": [{
                    "type": "cylinder", "radius": 3.25, "height": "base_z + 2",
                    "at": [12, 10, -1], "placements": [[0, 0, 0], [36, 0, 0]],
                }],
            },
            {
                "name": "upright",
                "add": [{
                    "type": "box", "size": [6, "base_y", "wall_h"],
                    "at": [27, 0, "base_z"],
                }],
            },
        ],
        "verification": {
            "tolerance": 0.05,
            "overall_bounding_box": {"size": [60, 40, 36], "min": [0, 0, 0]},
            "solid_count": 2,
            "component_count": 2,
            "components": {
                "base_plate": {
                    "solid_count": 1,
                    "bounding_box": {"size": [60, 40, 6]},
                },
                "upright": {
                    "solid_count": 1,
                    "bounding_box": {"size": [6, 40, 30], "min": [27, 0, 6]},
                },
            },
        },
    }
    calls = []

    def completion(messages, tools, **kwargs):
        round_index = len(calls)
        schema_names = {item["function"]["name"] for item in tools}
        assert {"generate_cad_design", "generate_cad_model", "open_cad_viewer"} <= schema_names
        if round_index == 0:
            definition = next(
                item for item in tools
                if item["function"]["name"] == "generate_cad_design"
            )
            assert definition["function"]["parameters"]["properties"]["design"]["type"] == "object"
            calls.append("generate_cad_design")
            return _response(
                "✿FUNCTION✿: generate_cad_design ✿ARGS✿: "
                + json.dumps({
                    "filename": str(model), "design": design, "formats": "step",
                })
            )
        if round_index == 1:
            assert "Generated and verified" in messages[-1]["content"]
            calls.append("open_cad_viewer")
            return _response(
                "✿FUNCTION✿: open_cad_viewer ✿ARGS✿: "
                + json.dumps({"filename": str(model), "open_browser": False})
            )
        assert "CAD Viewer ready" in messages[-1]["content"]
        calls.append("final")
        return _response("The bracket was generated, verified, and handed to the CAD Viewer.")

    engine.PERMISSION_MODE = "full-auto"
    engine.ALLOWED_PATHS = [tmp_path]
    monkeypatch.setattr(engine, "_create_completion_with_fallback", completion)
    answer = engine.run_agent(
        [{"role": "user", "content": "Create and preview a parametric CAD angle bracket"}],
        max_turns=3,
        system_prompt="base",
        tools_def=_cad_tool_definitions(engine),
        allowed_tools={"generate_cad_design", "generate_cad_model", "open_cad_viewer"},
    )
    assert calls == ["generate_cad_design", "open_cad_viewer", "final"]
    assert "generated, verified" in answer
    report = json.loads(model.with_suffix(".report.json").read_text(encoding="utf-8"))
    assert report["request_verification"]["ok"] is True
    assert report["assembly_interference"]["interferences"] == []
    assert report["solid_count"] == 2
    assert report["bounding_box"]["size"] == pytest.approx([60, 40, 36])
    assert model.with_suffix(".preview.png").stat().st_size > 0


def test_agent_loop_advanced_build123d_call_is_structured_and_verified(
        engine, tmp_path, monkeypatch):
    model = tmp_path / "filleted mounting plate.step"
    source = """from build123d import Align, Axis, Box, Cylinder, Pos

def gen_step():
    body = Box(
        PARAMS["length"], PARAMS["width"], PARAMS["height"],
        align=(Align.MIN, Align.MIN, Align.MIN),
    )
    body = body.fillet(PARAMS["fillet"], body.edges().filter_by(Axis.Z))
    for x in PARAMS["hole_x"]:
        body = body - Pos(x, PARAMS["width"] / 2, -1) * Cylinder(
            PARAMS["hole_radius"], PARAMS["height"] + 2,
            align=(Align.CENTER, Align.CENTER, Align.MIN),
        )
    boss = Pos(PARAMS["length"] / 2, PARAMS["width"] / 2, PARAMS["height"]) * Cylinder(
        PARAMS["boss_radius"], PARAMS["boss_height"],
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    )
    result = body + boss
    result.label = "filleted_mounting_plate"
    return result
"""
    parameters = {
        "length": 80, "width": 50, "height": 8, "fillet": 2,
        "hole_x": [15, 65], "hole_radius": 3.4,
        "boss_radius": 10, "boss_height": 12,
    }
    verification = {
        "tolerance": 0.05,
        "overall_bounding_box": {"size": [80, 50, 20], "min": [0, 0, 0]},
        "solid_count": 1,
    }
    calls = 0

    def completion(messages, tools, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            definition = next(
                item for item in tools
                if item["function"]["name"] == "generate_cad_model"
            )
            required = definition["function"]["parameters"]["required"]
            assert "verification" in required
            assert definition["function"]["parameters"]["properties"]["parameters"]["type"] == "object"
            return _response(
                "✿FUNCTION✿: generate_cad_model ✿ARGS✿: "
                + json.dumps({
                    "filename": str(model), "source": source,
                    "parameters": parameters, "verification": verification,
                    "formats": "step",
                })
            )
        assert "Request-specific checks" in messages[-1]["content"]
        return _response("The advanced mounting plate passed its engineering checks.")

    engine.PERMISSION_MODE = "full-auto"
    engine.ALLOWED_PATHS = [tmp_path]
    monkeypatch.setattr(engine, "_create_completion_with_fallback", completion)
    answer = engine.run_agent(
        [{"role": "user", "content": "Generate a filleted build123d CAD mounting plate"}],
        max_turns=2,
        system_prompt="base",
        tools_def=_cad_tool_definitions(engine),
        allowed_tools={"generate_cad_design", "generate_cad_model", "open_cad_viewer"},
    )
    assert calls == 2
    assert "passed" in answer
    report = json.loads(model.with_suffix(".report.json").read_text(encoding="utf-8"))
    assert report["request_verification"]["ok"] is True
    assert report["validity"]["ok"] is True
    assert report["solid_count"] == 1
    assert report["bounding_box"]["size"] == pytest.approx([80, 50, 20])
    assert model.with_suffix(".preview.png").stat().st_size > 0


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
        model, source, json.dumps(params), "step,stl,3mf,glb,brep", timeout=900,
        verification=json.dumps({
            "tolerance": 0.05,
            "overall_bounding_box": {"size": [80, 50, 20]},
            "solid_count": 1,
        }),
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
    assert report["request_verification"]["ok"] is True
    assert report["request_verification"]["provided"] is True

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


def test_generated_source_refuses_overlapping_assembly(tmp_path):
    model = tmp_path / "overlapping_source.step"
    source = """from build123d import Box, Compound, Pos

def gen_step():
    left = Box(10, 10, 10)
    right = Pos(5, 0, 0) * Box(10, 10, 10)
    return Compound(children=[left, right])
"""
    result = cad.generate_cad_model(model, source, "{}", "step", timeout=300)
    assert "assembly_interference" in result
    assert "solid[0] vs solid[1]" in result
    report = json.loads(model.with_suffix(".report.json").read_text(encoding="utf-8"))
    assert report["assembly_interference"]["interferences"][0]["volume"] == pytest.approx(500)


def test_declarative_two_storey_house_round_trip(tmp_path):
    """Regression for the real GLM workload: complex but compact and bounded."""
    p = {
        "width": 180, "depth": 120, "ground_h": 35, "first_h": 30,
        "wall": 3, "roof_t": 4, "roof_overhang": 12, "terrace_h": 12,
        "door_w": 18, "door_h": 25, "ground_window_w": 20,
        "ground_window_h": 15, "first_window_w": 25,
        "first_window_h": 16, "balcony_depth": 15, "column_r": 3,
    }
    ground_windows = [[25, -1, 10], [65, -1, 10], [115, -1, 10], [155, -1, 10]]
    first_windows = [[25, -1, 47], [65, -1, 47], [110, -1, 47], [145, -1, 47]]
    components = [
        {
            "name": "ground_floor_walls",
            "add": [{"type": "box", "size": ["width", "depth", "ground_h"]}],
            "cut": [
                {"type": "box", "size": ["width - 2*wall", "depth - 2*wall", "ground_h + 2"],
                 "at": ["wall", "wall", -1]},
                {"type": "box", "size": ["door_w", "wall + 2", "door_h"],
                 "at": ["(width-door_w)/2", -1, 0]},
                {"type": "box", "size": ["ground_window_w", "wall + 2", "ground_window_h"],
                 "placements": ground_windows},
            ],
        },
        {"name": "first_floor_slab", "add": [{"type": "box", "size": ["width", "depth", 4],
                                                   "at": [0, 0, "ground_h"]}]},
        {
            "name": "first_floor_walls",
            "add": [{"type": "box", "size": ["width", "depth", "first_h"],
                     "at": [0, 0, "ground_h + 4"]}],
            "cut": [
                {"type": "box", "size": ["width - 2*wall", "depth - 2*wall", "first_h + 2"],
                 "at": ["wall", "wall", "ground_h + 3"]},
                {"type": "box", "size": ["first_window_w", "wall + 2", "first_window_h"],
                 "placements": first_windows},
            ],
        },
        {"name": "central_hall_left", "add": [{"type": "box", "size": ["wall", 80, "ground_h"],
                                                   "at": [70, 20, 0]}]},
        {"name": "central_hall_right", "add": [{"type": "box", "size": ["wall", 80, "ground_h"],
                                                    "at": [107, 20, 0]}]},
        {"name": "room_dividers", "add": [
            {"type": "box", "size": [67, "wall", "ground_h"], "at": [3, 60, 0]},
            {"type": "box", "size": [67, "wall", "ground_h"], "at": [110, 60, 0]},
        ]},
        {"name": "roof", "add": [{"type": "box", "size": ["width", "depth + roof_overhang", "roof_t"],
                                      "at": [0, "-roof_overhang", "ground_h + 4 + first_h"]}]},
        {"name": "rear_terrace_wall", "add": [{"type": "box", "size": ["width", "wall", "terrace_h"],
                                                   "at": [0, "depth-wall", "ground_h+4+first_h+roof_t"]}]},
        {"name": "balcony", "add": [{"type": "box", "size": [50, "balcony_depth", 3],
                                         "at": [65, "-balcony_depth", "ground_h"]}]},
        {"name": "balcony_columns", "add": [{"type": "cylinder", "radius": "column_r", "height": "ground_h - 2",
                                                 "at": [0, 0, 2],
                                                 "placements": [[72, -10, 0], [108, -10, 0]]}]},
        {"name": "porch", "add": [{"type": "box", "size": [50, 18, 2], "at": [65, -18, 0]}]},
        {"name": "chimney", "add": [{"type": "box", "size": [12, 10, 18],
                                         "at": [140, 85, "ground_h+4+first_h+roof_t"]}]},
    ]
    # Ten independently sized steps keep the test representative of the prompt
    # while the JSON remains much smaller and safer than generated Python.
    components.append({
        "name": "staircase",
        "add": [
            {"type": "box", "size": [30, 6, (index + 1) * 3.5], "at": [75, 18 + index * 6, 0]}
            for index in range(10)
        ],
    })
    design = {
        "schema_version": 1, "name": "two_storey_house", "units": "mm",
        "parameters": p, "components": components,
        "verification": {
            "tolerance": 0.05,
            "overall_bounding_box": {"size": [180, 138, 91]},
            "component_count": 13,
            "components": {
                "roof": {
                    "solid_count": 1,
                    "bounding_box": {
                        "size": [180, 132, 4],
                        "min": [0, -12, 69],
                    },
                },
            },
        },
    }
    model = tmp_path / "two_storey_house.step"
    result = cad.generate_cad_design(model, json.dumps(design), "step,stl", timeout=900)
    assert "Generated and verified" in result, result
    assert "Named components: 13" in result
    report = json.loads(model.with_suffix(".report.json").read_text(encoding="utf-8"))
    assert report["validity"]["ok"] is True
    assert report["validity"]["scope"] == "per-solid"
    assert report["assembly_interference"]["interferences"] == []
    assert report["component_count"] == 13
    assert report["request_verification"]["ok"] is True
    assert report["request_verification"]["provided"] is True
    assert report["solid_count"] >= 13
    assert report["bounding_box"]["size"][0] == pytest.approx(180)
    assert report["bounding_box"]["size"][1] == pytest.approx(138)
    assert model.with_suffix(".preview.png").stat().st_size > 0


def test_declarative_design_refuses_overlapping_assembly_by_default(tmp_path):
    design = {
        "schema_version": 1, "units": "mm", "parameters": {},
        "components": [
            {"name": "a", "add": [{"type": "box", "size": [10, 10, 10]}]},
            {"name": "b", "add": [{"type": "box", "size": [10, 10, 10], "at": [5, 0, 0]}]},
        ],
    }
    model = tmp_path / "overlap.step"
    result = cad.generate_cad_design(model, json.dumps(design), "step", timeout=300)
    assert "assembly_interference" in result
    assert "a vs b" in result
    report = json.loads(model.with_suffix(".report.json").read_text(encoding="utf-8"))
    assert report["assembly_interference"]["interferences"][0]["volume"] == pytest.approx(500)
    assert report["assembly_interference"]["interferences"][0]["component_a"] == "a"
    assert report["assembly_interference"]["interferences"][0]["component_b"] == "b"


def test_declarative_mismatch_withholds_secondary_exports(tmp_path):
    design = {
        "schema_version": 1, "units": "mm", "parameters": {},
        "components": [
            {"name": "body", "add": [{"type": "box", "size": [10, 20, 30]}]},
        ],
        "verification": {
            "tolerance": 0.01,
            "overall_bounding_box": {"size": [11, 20, 30]},
            "solid_count": 1,
            "component_count": 1,
        },
    }
    model = tmp_path / "mismatch.step"
    result = cad.generate_cad_design(model, json.dumps(design), "step,stl", timeout=300)
    assert "verification_mismatch" in result
    assert "overall_bounding_box.size" in result
    assert model.is_file()
    assert model.with_suffix(".report.json").is_file()
    assert model.with_suffix(".preview.png").is_file()
    assert not model.with_suffix(".stl").exists()
