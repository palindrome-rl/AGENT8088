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
    # Re-running the identical spec is idempotent: nothing is built twice and
    # the same guidance comes back.
    again = cad_project.create(
        manifest, "robot", parts,
        parameters={"clearance": 0.3}, verification={"solid_count": 2}, formats="step,stl",
    )
    assert "1 custom part(s) still need cad_project_add_component" in again

    # Drift on a part that is NOT yet built is allowed now -- that is what makes
    # a failed part correctable in place instead of forcing a new manifest.
    # (Drift on a *built* part is still refused: see
    # test_create_still_refuses_to_change_an_already_built_part.)
    changed = cad_project.create(
        manifest, "robot", [_custom_part("Base", "a revised description")],
        parameters={"clearance": 1.0}, verification={"solid_count": 2}, formats="step,stl",
    )
    assert "different project specification" not in changed
    saved = json.loads(manifest.read_text())
    assert saved["parts"]["Base"]["description"] == "a revised description"
    assert saved["parameters"] == {"clearance": 1.0}


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


def _warehouse_part(name, kind, params):
    return {"name": name, "kind": kind, "params": params}


def test_failed_warehouse_build_is_recoverable_on_the_same_manifest(tmp_path, monkeypatch):
    """The deadlock from a real 50-turn run: create() persisted the manifest
    even when a warehouse part failed, then refused the corrected retry as
    "a different project specification" while add_component refused the same
    part as "already built" -- leaving a new filename as the only way out."""
    manifest = tmp_path / "robot" / "project.cadproject.json"
    built_calls = []

    def fake_build(output, kind, params):
        built_calls.append(params.get("name_marker"))
        if params.get("bad"):
            return {"ok": False, "error": "invalid param"}
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_bytes(b"STEP")
        return {"ok": True, "ports": {"bore": {"at": [0, 0, 0], "axis": [0, 0, 1]}}}

    monkeypatch.setattr(cad_project.cad, "build_warehouse_component", fake_build)

    parts = [
        _warehouse_part("GoodGear", "warehouse.gear", {"name_marker": "good"}),
        _warehouse_part("BadGear", "warehouse.gear", {"name_marker": "bad", "bad": True}),
    ]
    first = cad_project.create(manifest, "robot", parts)
    assert "could not be built" in first
    assert "BadGear" in first
    assert "SAME filename" in first

    # The corrected retry lands on the same manifest instead of being refused.
    parts[1] = _warehouse_part("BadGear", "warehouse.gear", {"name_marker": "fixed"})
    built_calls.clear()
    second = cad_project.create(manifest, "robot", parts)
    assert "different project specification" not in second
    assert "Auto-built 1 warehouse part(s)" in second
    # The already-good part is carried forward, not rebuilt.
    assert built_calls == ["fixed"]
    saved = json.loads(manifest.read_text())
    assert saved["components"]["GoodGear"]["status"] == "built"
    assert saved["components"]["BadGear"]["status"] == "built"


def test_add_component_does_not_claim_an_unbuilt_warehouse_part_was_built(
    tmp_path, monkeypatch
):
    manifest = tmp_path / "robot" / "project.cadproject.json"
    monkeypatch.setattr(
        cad_project.cad, "build_warehouse_component",
        lambda *a, **k: {"ok": False, "error": "invalid param"},
    )
    cad_project.create(
        manifest, "robot", [_warehouse_part("Screw", "warehouse.fastener", {})]
    )

    result = cad_project.add_component(
        manifest, "Screw", "def gen_step(): pass", verification={"solid_count": 1}
    )
    assert "already built" not in result
    assert "has not succeeded yet" in result
    assert "cad_project_create" in result


def test_create_still_refuses_to_change_an_already_built_part(tmp_path, monkeypatch):
    manifest = tmp_path / "robot" / "project.cadproject.json"

    def fake_build(output, kind, params):
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_bytes(b"STEP")
        return {"ok": True, "ports": {}}

    monkeypatch.setattr(cad_project.cad, "build_warehouse_component", fake_build)
    parts = [_warehouse_part("Gear", "warehouse.gear", {"module": 1.5})]
    assert "Auto-built" in cad_project.create(manifest, "robot", parts)

    drifted = [_warehouse_part("Gear", "warehouse.gear", {"module": 99})]
    result = cad_project.create(manifest, "robot", drifted)
    assert "already built and verified" in result
    assert "Gear" in result


def test_status_lists_declared_parts_with_kind_and_state(tmp_path, monkeypatch):
    manifest = tmp_path / "robot" / "project.cadproject.json"
    monkeypatch.setattr(
        cad_project.cad, "build_warehouse_component",
        lambda *a, **k: {"ok": False, "error": "invalid param"},
    )
    cad_project.create(manifest, "robot", [
        _custom_part("Housing"),
        _warehouse_part("Screw", "warehouse.fastener", {}),
    ])

    report = cad_project.status(manifest)
    assert "Parts: 0/2 built" in report
    assert "Housing [custom]" in report
    assert "Screw [warehouse.fastener]" in report
    # A part that was declared but never built must be visible, not omitted.
    assert "not built" in report


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


def test_arg_parse_error_gets_actionable_guidance_not_just_the_raw_parser_error(
    engine, monkeypatch
):
    """A real 50-turn run lost 8 turns to a model re-sending the same
    unparseable payload verbatim after seeing only the generic parser
    message. The loop's own "no progress" breaker ends a run after just one
    non-executing turn (verified directly, not assumed), so there is only
    ever one real chance for the model to see guidance before the run
    terminates -- the fix therefore has to escalate on that first occurrence,
    not build up to it over several turns."""
    seen_final_messages = []
    broken = "✿FUNCTION✿: cad_project_create ✿ARGS✿: {\"filename\": \"a/b.cadproject.json\", oops"

    def completion(messages, tools, **kwargs):
        seen_final_messages.append(list(messages))
        return _response(broken)

    # full-auto so the write escalates straight through and the loop actually
    # reaches run_tool's parse-error branch, instead of cycling on the
    # permission-escalation prompt (a different, already-covered path).
    monkeypatch.setattr(engine, "PERMISSION_MODE", "full-auto")
    monkeypatch.setattr(engine, "_create_completion_with_fallback", completion)
    result = engine.run_agent(
        [{"role": "user", "content": "make a CAD project"}],
        max_turns=8,
        system_prompt="base",
        tools_def=[],
        allowed_tools={"cad_project_create"},
    )

    # The run ends well short of the 8-turn limit (the pre-existing no-progress
    # breaker, not a new turn-limit error) -- exactly the "burns every turn"
    # failure mode this change removes.
    assert "reached the 8-turn limit" not in result
    # The actionable guidance was appended to the conversation the model saw
    # on its next (and, structurally, only remaining) completion call.
    last_round = seen_final_messages[-1]
    actionable = [
        m for m in last_round
        if m.get("role") == "user"
        and "could not be parsed as JSON" in str(m.get("content", ""))
    ]
    assert actionable, "expected actionable parse-error guidance in the next completion call"
    assert "omit 'verification'" in actionable[0]["content"]
    assert "Do not re-send the same payload unchanged" in actionable[0]["content"]
