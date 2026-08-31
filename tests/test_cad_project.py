"""Checkpointed CAD-project workflow and agent orchestration coverage."""

from __future__ import annotations

import json
from pathlib import Path

from agent8088 import cad_project


def _response(content: str, finish_reason: str = "stop"):
    return type(
        "R",
        (),
        {
            "choices": [
                type(
                    "C",
                    (),
                    {
                        "message": type("M", (), {"content": content})(),
                        "finish_reason": finish_reason,
                    },
                )()
            ]
        },
    )()


def _fake_component_artifacts(output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"STEP")
    output.with_suffix(".step.py").write_text("def gen_step():\n    pass\n")
    output.with_suffix(".report.json").write_text(
        json.dumps(
            {
                "solid_count": 1,
                "volume": 24,
                "bounding_box": {"size": [2, 3, 4]},
            }
        )
    )


def _custom_part(name, description="a custom part"):
    return {"name": name, "kind": "custom", "description": description}


def test_project_create_is_idempotent_but_rejects_spec_drift(tmp_path):
    manifest = tmp_path / "robot" / "project.cadproject.json"
    parts = [_custom_part("Base")]
    created = cad_project.create(
        manifest, "robot", parts,
        parameters={"clearance": 0.3}, verification={"solid_count": 2}, formats="step,stl",
    )
    assert "Created staged CAD project" in created
    assert "Auto-built 1 warehouse part(s) with zero model turns" not in created
    assert "1 custom part(s) still need cad_project_add_component" in created
    # Resuming with the identical spec returns the checkpoint status, not a
    # second "Created" message.
    assert "CAD project: robot" in cad_project.create(
        manifest, "robot", parts,
        parameters={"clearance": 0.3}, verification={"solid_count": 2}, formats="step,stl",
    )
    mismatch = cad_project.create(
        manifest, "robot", parts,
        parameters={"clearance": 1.0}, verification={"solid_count": 2}, formats="step,stl",
    )
    assert "different project specification" in mismatch


def test_component_build_is_checkpointed_and_exact_retry_is_cached(
    tmp_path, monkeypatch
):
    manifest = tmp_path / "robot" / "project.cadproject.json"
    cad_project.create(manifest, "robot", [_custom_part("Base")], verification={"solid_count": 1}, formats="step")
    calls = []

    def fake_generate(output, source, parameters, formats, **kwargs):
        calls.append((Path(output), source, json.loads(parameters), formats, kwargs))
        _fake_component_artifacts(Path(output))
        return f"Generated and verified {Path(output).name}"

    monkeypatch.setattr(cad_project.cad, "generate_cad_model", fake_generate)
    source = "from build123d import Box\ndef gen_step():\n    return Box(2, 3, 4)"
    first = cad_project.add_component(
        manifest, "Base", source, {}, {"solid_count": 1}
    )
    second = cad_project.add_component(
        manifest, "Base", source, {}, {"solid_count": 1}
    )

    assert "Built and checkpointed" in first
    assert "already built and verified" in second
    assert len(calls) == 1
    saved = json.loads(manifest.read_text())
    assert saved["components"]["Base"]["status"] == "built"
    assert saved["components"]["Base"]["source"].endswith("Base.step.py")
    assert "def gen_step" not in manifest.read_text()


def test_component_failure_is_preserved_for_targeted_repair(tmp_path, monkeypatch):
    manifest = tmp_path / "robot" / "project.cadproject.json"
    cad_project.create(manifest, "robot", [_custom_part("Finger")], verification={"solid_count": 1}, formats="step")
    monkeypatch.setattr(
        cad_project.cad,
        "generate_cad_model",
        lambda *args, **kwargs: "CAD generation failed: invalid fillet",
    )

    result = cad_project.add_component(
        manifest,
        "Finger",
        "from build123d import Box\ndef gen_step():\n    return Box(1, 1, 1)",
        {},
        {"solid_count": 1},
    )

    assert "Repair only this component" in result
    assert "2 repair attempt(s) left" in result
    saved = json.loads(manifest.read_text())
    assert saved["components"]["Finger"]["status"] == "failed"
    assert saved["components"]["Finger"]["attempts"] == 1
    assert "invalid fillet" in saved["components"]["Finger"]["last_error"]


def test_add_component_rejects_undeclared_or_non_custom_name(tmp_path):
    manifest = tmp_path / "robot" / "project.cadproject.json"
    cad_project.create(manifest, "robot", [_custom_part("Finger")], verification={"solid_count": 1})

    undeclared = cad_project.add_component(
        manifest, "Ghost", "def gen_step(): pass", verification={"solid_count": 1}
    )
    assert "was not declared" in undeclared


def test_add_component_repair_budget_is_bounded(tmp_path, monkeypatch):
    manifest = tmp_path / "robot" / "project.cadproject.json"
    cad_project.create(manifest, "robot", [_custom_part("Finger")], verification={"solid_count": 1})
    monkeypatch.setattr(
        cad_project.cad,
        "generate_cad_model",
        lambda *args, **kwargs: "CAD generation failed: still broken",
    )
    source = "from build123d import Box\ndef gen_step():\n    return Box(1, 1, 1)"
    for _ in range(cad_project.MAX_CUSTOM_REPAIR_ATTEMPTS):
        result = cad_project.add_component(manifest, "Finger", source, verification={"solid_count": 1})
        assert "Repair only this component" in result

    exhausted = cad_project.add_component(manifest, "Finger", source, verification={"solid_count": 1})
    assert "repair budget" in exhausted
    saved = json.loads(manifest.read_text())
    assert saved["components"]["Finger"]["attempts"] == cad_project.MAX_CUSTOM_REPAIR_ATTEMPTS


def test_finalize_refuses_when_parts_are_not_all_built(tmp_path, monkeypatch):
    manifest = tmp_path / "robot" / "project.cadproject.json"
    cad_project.create(manifest, "robot", [_custom_part("Missing")], verification={"solid_count": 1})
    called = []
    monkeypatch.setattr(
        cad_project.cad,
        "_run_worker",
        lambda *args, **kwargs: called.append(1) or {"ok": True},
    )

    result = cad_project.finalize(manifest, verification={"solid_count": 1})

    assert "are not built yet" in result
    assert "Missing" in result
    assert called == []


def test_finalize_assembles_from_declared_mates_with_no_assembly_argument(tmp_path, monkeypatch):
    manifest = tmp_path / "robot" / "project.cadproject.json"
    parts = [_custom_part("Base"), _custom_part("Finger")]
    mates = [{"type": "press_fit", "a": "Finger.pin", "b": "Base.socket"}]
    cad_project.create(manifest, "robot", parts, mates, verification={"solid_count": 2}, formats="step,stl")

    def fake_generate(output, *_args, **_kwargs):
        _fake_component_artifacts(Path(output))
        return f"Generated and verified {Path(output).name}"

    monkeypatch.setattr(cad_project.cad, "generate_cad_model", fake_generate)
    source = "from build123d import Box\ndef gen_step():\n    return Box(2, 3, 4)"
    for name in ("Base", "Finger"):
        assert "Built and checkpointed" in cad_project.add_component(
            manifest, name, source, {}, {"solid_count": 1}
        )

    seen_requests = []

    def fake_worker(request, timeout):
        seen_requests.append(request)
        output = Path(request["output"])
        output.write_bytes(b"ASSEMBLY STEP")
        output.with_suffix(".stl").write_bytes(b"STL")
        Path(request["report"]).write_text("{}")
        Path(request["preview"]).write_bytes(b"PNG")
        return {
            "ok": True,
            "solid_count": 2,
            "volume": 48,
            "bounding_box": {"size": [12, 3, 4]},
            "component_names": ["Base", "Finger"],
        }

    monkeypatch.setattr(cad_project.cad, "_run_worker", fake_worker)
    result = cad_project.finalize(manifest, verification={"solid_count": 2, "component_count": 2}, formats="step,stl")

    assert "Finalized and verified" in result
    saved = json.loads(manifest.read_text())
    assert saved["assembly"]["status"] == "built"
    assert (manifest.parent / "robot.step").is_file()
    assert (manifest.parent / "robot.stl").is_file()
    # No at/rotate was ever supplied by the caller -- the assembly JSON sent
    # to the worker carries components+mates, derived entirely from what
    # create() already declared, not a placement object finalize() accepted.
    assembly_spec = json.loads(Path(seen_requests[0]["assembly"]).read_text())
    assert assembly_spec["mates"] == mates
    assert {c["name"] for c in assembly_spec["components"]} == {"Base", "Finger"}
    assert "occurrences" not in assembly_spec


def test_project_tools_publish_structured_schemas(engine):
    definitions = {
        item["function"]["name"]: item["function"]["parameters"]
        for item in engine.build_tools_def(engine.TOOL_SPECS)
    }
    create = definitions["cad_project_create"]
    assert create["properties"]["parameters"]["type"] == "object"
    assert create["properties"]["parts"]["type"] == "array"
    assert create["properties"]["mates"]["type"] == "array"
    component = definitions["cad_project_add_component"]
    assert component["properties"]["verification"]["minProperties"] == 1
    assert component["properties"]["ports"]["type"] == "object"
    final = definitions["cad_project_finalize"]
    # finalize no longer takes a placement object -- the model never
    # authors at/rotate; positions come from the mates declared at create().
    assert "assembly" not in final["properties"]
    assert final["properties"]["verification"]["type"] == "object"
    assert engine.TOOL_SPECS["cad_project_status"]["mode"] == "read_text"


def test_project_mutation_inherits_the_existing_write_permission_gate(
    engine, tmp_path, monkeypatch
):
    engine.PERMISSION_MODE = "readonly"
    engine.ALLOWED_PATHS = [tmp_path]
    manifest = tmp_path / "robot" / "project.cadproject.json"
    monkeypatch.setattr(
        engine.cad_project,
        "create",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("backend must not run before approval")
        ),
    )

    result = engine.exec_tool(
        "cad_project_create",
        json.dumps(
            {
                "filename": str(manifest),
                "name": "robot",
                "parts": [{"name": "Base", "kind": "custom", "description": "a base"}],
                "parameters": {},
                "verification": {"solid_count": 1},
            }
        ),
    )

    assert result.startswith("ESCALATION_REQUEST\x1f")
    assert not manifest.exists()


def test_cad_overflow_switches_to_project_tools_without_doubling_budget(
    engine, monkeypatch
):
    calls = []
    tools_seen = []

    def completion(messages, tools, max_tokens=None, **kwargs):
        calls.append({"messages": list(messages), "max_tokens": max_tokens})
        tools_seen.append({item["function"]["name"] for item in tools})
        if len(calls) == 1:
            return _response("partial source " * 200, "length")
        return _response("done")

    monkeypatch.setattr(engine, "_create_completion_with_fallback", completion)
    available = {
        "generate_cad_model",
        "generate_cad_design",
        "cad_project_create",
        "cad_project_add_component",
        "cad_project_finalize",
        "cad_project_status",
    }
    definitions = [
        item
        for item in engine.build_tools_def(engine.TOOL_SPECS)
        if item["function"]["name"] in available
    ]
    result = engine.run_agent(
        [{"role": "user", "content": "Generate a complex robotic CAD assembly"}],
        max_turns=2,
        system_prompt="base",
        tools_def=definitions,
        allowed_tools=available,
    )

    assert result == "done"
    assert calls[0]["max_tokens"] == engine.MAX_COMPLETION_TOKENS
    assert calls[1]["max_tokens"] == engine.MAX_COMPLETION_TOKENS
    assert "generate_cad_model" in tools_seen[0]
    assert "generate_cad_model" not in tools_seen[1]
    assert "cad_project_create" in tools_seen[1]
    retry_messages = calls[1]["messages"]
    assert not any("partial source" in item["content"] for item in retry_messages)
    assert "checkpointed CAD project" in retry_messages[-1]["content"]


def test_only_one_checkpoint_call_executes_per_model_response(engine, monkeypatch):
    manifest = "robot/project.cadproject.json"
    emitted = (
        "✿FUNCTION✿: cad_project_create ✿ARGS✿: "
        + json.dumps({"filename": manifest, "name": "robot", "parameters": {}})
        + "\n✿FUNCTION✿: cad_project_add_component ✿ARGS✿: "
        + json.dumps(
            {
                "filename": manifest,
                "name": "Base",
                "source": "def gen_step(): pass",
                "verification": {"solid_count": 1},
            }
        )
    )
    responses = iter((_response(emitted), _response("waiting for checkpoint")))
    executed = []

    monkeypatch.setattr(
        engine,
        "_create_completion_with_fallback",
        lambda *args, **kwargs: next(responses),
    )
    monkeypatch.setattr(
        engine,
        "exec_tool",
        lambda name, args, **kwargs: executed.append(name) or "checkpoint created",
    )
    result = engine.run_agent(
        [{"role": "user", "content": "Generate a robotic CAD assembly"}],
        max_turns=2,
        system_prompt="base",
        tools_def=[],
        allowed_tools={"cad_project_create", "cad_project_add_component"},
    )

    assert result == "waiting for checkpoint"
    assert executed == ["cad_project_create"]
