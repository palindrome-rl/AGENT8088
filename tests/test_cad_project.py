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


def test_project_create_is_idempotent_but_rejects_spec_drift(tmp_path):
    manifest = tmp_path / "robot" / "project.cadproject.json"
    created = cad_project.create(
        manifest,
        "robot",
        {"clearance": 0.3},
        {"solid_count": 2},
        "step,stl",
    )
    assert "Created staged CAD project" in created
    assert "Components: 0/64" in cad_project.create(
        manifest,
        "robot",
        {"clearance": 0.3},
        {"solid_count": 2},
        "step,stl",
    )
    mismatch = cad_project.create(
        manifest,
        "robot",
        {"clearance": 1.0},
        {"solid_count": 2},
        "step,stl",
    )
    assert "different project specification" in mismatch


def test_component_build_is_checkpointed_and_exact_retry_is_cached(
    tmp_path, monkeypatch
):
    manifest = tmp_path / "robot" / "project.cadproject.json"
    cad_project.create(manifest, "robot", {}, {"solid_count": 1}, "step")
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
    cad_project.create(manifest, "robot", {}, {"solid_count": 1}, "step")
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
    saved = json.loads(manifest.read_text())
    assert saved["components"]["Finger"]["status"] == "failed"
    assert "invalid fillet" in saved["components"]["Finger"]["last_error"]


def test_finalize_validates_occurrences_before_worker(tmp_path, monkeypatch):
    manifest = tmp_path / "robot" / "project.cadproject.json"
    cad_project.create(manifest, "robot", {}, {"solid_count": 1}, "step")
    called = []
    monkeypatch.setattr(
        cad_project.cad,
        "_run_worker",
        lambda *args, **kwargs: called.append(1) or {"ok": True},
    )

    result = cad_project.finalize(
        manifest,
        {
            "occurrences": [{"name": "Missing", "component": "Missing"}],
            "verification": {"solid_count": 1},
        },
    )

    assert "is not built and verified" in result
    assert called == []


def test_finalize_checkpoints_verified_artifact_bundle(tmp_path, monkeypatch):
    manifest = tmp_path / "robot" / "project.cadproject.json"
    cad_project.create(manifest, "robot", {}, {"solid_count": 2}, "step,stl")

    def fake_generate(output, *_args, **_kwargs):
        _fake_component_artifacts(Path(output))
        return f"Generated and verified {Path(output).name}"

    monkeypatch.setattr(cad_project.cad, "generate_cad_model", fake_generate)
    source = "from build123d import Box\ndef gen_step():\n    return Box(2, 3, 4)"
    for name in ("Base", "Finger"):
        assert "Built and checkpointed" in cad_project.add_component(
            manifest, name, source, {}, {"solid_count": 1}
        )

    def fake_worker(request, timeout):
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
    result = cad_project.finalize(
        manifest,
        {
            "occurrences": [
                {"name": "Base", "component": "Base"},
                {"name": "Finger", "component": "Finger", "at": [10, 0, 0]},
            ],
            "verification": {"solid_count": 2, "component_count": 2},
        },
        "step,stl",
    )

    assert "Finalized and verified" in result
    saved = json.loads(manifest.read_text())
    assert saved["assembly"]["status"] == "built"
    assert (manifest.parent / "robot.step").is_file()
    assert (manifest.parent / "robot.stl").is_file()


def test_project_tools_publish_structured_schemas(engine):
    definitions = {
        item["function"]["name"]: item["function"]["parameters"]
        for item in engine.build_tools_def(engine.TOOL_SPECS)
    }
    assert definitions["cad_project_create"]["properties"]["parameters"]["type"] == "object"
    component = definitions["cad_project_add_component"]
    assert component["properties"]["verification"]["minProperties"] == 1
    final = definitions["cad_project_finalize"]
    occurrence = final["properties"]["assembly"]["properties"]["occurrences"]
    assert occurrence["items"]["required"] == ["name", "component"]
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
