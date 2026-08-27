"""convert_cad and create_cad_part, the tools — engine-level wiring and permission gating.

Unit coverage for the conversion logic itself lives in tests/test_cad.py; this
file covers what only exists once the tools are registered: mode=write_text
sharing every write guard (same reasoning as convert_document — a dozen sites
key on that mode, a private mode would need every one added), and that the
pipe-delimited tools.txt rows parsed correctly (a description containing '|'
gets silently truncated — a real bug hit once already this session on
convert_document's own row).
"""
import json
from pathlib import Path

import pytest


def _response(content):
    return type("R", (), {"choices": [type("C", (), {
        "message": type("M", (), {"content": content})(),
        "finish_reason": "stop",
    })()]})()


def _convert_cad(engine, path, fmt):
    return engine.exec_tool(
        "convert_cad", json.dumps({"filename": str(path), "format": fmt}))


def _create_cad_part(engine, path, shape, dimensions):
    return engine.exec_tool(
        "create_cad_part", json.dumps({"filename": str(path), "shape": shape, "dimensions": dimensions}))


def _generate_cad_model(engine, path, source, parameters="{}", formats="step,stl"):
    return engine.exec_tool("generate_cad_model", json.dumps({
        "filename": str(path), "source": source,
        "parameters": parameters, "formats": formats,
    }))


def _generate_cad_design(engine, path, design, formats="step,stl"):
    return engine.exec_tool("generate_cad_design", json.dumps({
        "filename": str(path), "design": design, "formats": formats,
    }))


def _validate_cad_model(engine, path, render=True):
    return engine.exec_tool("validate_cad_model", json.dumps({
        "filename": str(path), "render": render,
    }))


def _open_cad_viewer(engine, path, open_browser=True):
    return engine.exec_tool("open_cad_viewer", json.dumps({
        "filename": str(path), "open_browser": open_browser,
    }))


@pytest.fixture
def ready(engine, tmp_path):
    engine.PERMISSION_MODE = "full-auto"
    engine.ALLOWED_PATHS = [tmp_path]
    return engine


def test_convert_cad_is_registered_with_the_write_mode(engine):
    assert engine.TOOL_SPECS["convert_cad"]["mode"] == "write_text"


def test_create_cad_part_is_registered_with_the_write_mode(engine):
    assert engine.TOOL_SPECS["create_cad_part"]["mode"] == "write_text"


def test_advanced_cad_tools_are_registered_with_the_write_mode(engine):
    assert engine.TOOL_SPECS["generate_cad_design"]["mode"] == "write_text"
    assert engine.TOOL_SPECS["generate_cad_model"]["mode"] == "write_text"
    assert engine.TOOL_SPECS["validate_cad_model"]["mode"] == "write_text"


def test_cad_viewer_is_registered_with_the_existing_read_gate(engine):
    assert engine.TOOL_SPECS["open_cad_viewer"]["mode"] == "read_text"
    assert engine.TOOL_SPECS["open_cad_viewer"]["path_arg"] == "filename"


def test_cad_generation_request_disables_generic_execution_and_injects_real_artifacts_path(
        engine, monkeypatch):
    seen = {}

    def completion(messages, tools, system_prompt=None, **kwargs):
        seen["tools"] = tools
        seen["system"] = system_prompt
        return _response("done")

    monkeypatch.setattr(engine, "_create_completion_with_fallback", completion)
    tools = [
        {"type": "function", "function": {"name": name}}
        for name in (
            "execute_shell", "run_sandboxed", "write_file",
            "generate_cad_design", "generate_cad_model", "open_cad_viewer",
        )
    ]
    result = engine.run_agent(
        [{"role": "user", "content": "Generate a parametric CAD house with build123d"}],
        max_turns=1, system_prompt="base", tools_def=tools,
        allowed_tools={
            "execute_shell", "run_sandboxed", "write_file",
            "generate_cad_design", "generate_cad_model", "open_cad_viewer",
        },
    )
    assert result == "done"
    rendered = json.dumps(seen["tools"])
    assert "generate_cad_design" in rendered
    assert "execute_shell" not in rendered
    assert "run_sandboxed" not in rendered
    assert "write_file" not in rendered
    assert "generate_cad_model" not in rendered
    assert "open_cad_viewer" in rendered
    assert str(engine.ARTIFACTS_ROOT) in seen["system"]
    assert "Never guess C:/artifacts" in seen["system"]
    assert "use open_cad_viewer" in seen["system"]


def test_old_cad_turn_does_not_restrict_an_unrelated_followup(engine):
    assert engine._cad_generation_requested([
        {"role": "user", "content": "Generate a CAD bracket"},
        {"role": "assistant", "content": "done"},
        {"role": "user", "content": "Now tell me a joke"},
    ]) is False


def test_advanced_geometry_keeps_python_escape_hatch_available(engine):
    assert engine._advanced_cad_source_requested([
        {"role": "user", "content": "Generate a CAD impeller with lofts and fillets"},
    ]) is True


def test_cad_generation_stops_after_two_backend_failures(engine, monkeypatch):
    attempts = iter(("{}", '{"components":[]}', '{"schema_version":1}'))

    def completion(*args, **kwargs):
        design = next(attempts, '{"name":"still-looping"}')
        return _response(
            '✿FUNCTION✿: generate_cad_design ✿ARGS✿: '
            + json.dumps({"filename": "house.step", "design": design, "formats": "step"})
        )

    monkeypatch.setattr(engine, "_create_completion_with_fallback", completion)
    executions = []

    def execute(name, args, **kwargs):
        executions.append(name)
        return "CAD design generation failed: [invalid_design] bad component"

    monkeypatch.setattr(engine, "exec_tool", execute)
    engine.run_agent(
        [{"role": "user", "content": "Generate a CAD house using build123d"}],
        max_turns=4, system_prompt="base",
        tools_def=[{"type": "function", "function": {"name": "generate_cad_design"}}],
        allowed_tools={"generate_cad_design"},
    )
    assert executions == ["generate_cad_design", "generate_cad_design"]


def test_convert_cad_is_excluded_from_the_auditor(engine):
    """convert_cad is a deterministic built-in that verifies its own output on
    disk (output_path.exists() + byte count). The auditor runs in a disposable
    sandbox copy and cannot see the real Windows file the step produced, so it
    returns fail/unknown from its own blindness — pure noise that costs a model
    call and can revert correct work. So even with plan_audit on and a write
    closure mode, this tool must not be audited."""
    assert engine._plan_step_is_auditable("convert_cad", "") is False
    # A declared acceptance criterion is the strongest audit signal — but the
    # exclusion holds even there, because the tool's own disk check is the
    # real acceptance criterion and an auditor cannot improve on it.
    assert engine._plan_step_is_auditable("convert_cad", "file exists") is False
    # The exclusion is specific, not a blanket mute: a write_file step with the
    # same mode still gets audited, so the guard cannot be widened by accident.
    assert engine._plan_step_is_auditable("write_file", "") is True


def test_create_cad_part_is_excluded_from_the_auditor(engine):
    """Same reasoning as convert_cad: the tool verifies its own output on disk
    and is the only source of truth."""
    assert engine._plan_step_is_auditable("create_cad_part", "") is False
    assert engine._plan_step_is_auditable("create_cad_part", "file exists") is False
    assert engine._plan_step_is_auditable("write_file", "") is True


def test_advanced_cad_tools_are_excluded_from_the_auditor(engine):
    assert engine._plan_step_is_auditable("generate_cad_design", "") is False
    assert engine._plan_step_is_auditable("generate_cad_model", "") is False
    assert engine._plan_step_is_auditable("validate_cad_model", "") is False


def test_convert_cad_description_survived_the_pipe_delimited_registry(engine):
    desc = engine.TOOL_SPECS["convert_cad"]["description"]
    assert "convert" in desc.lower(), "description was cut short by a stray pipe"


def test_create_cad_part_description_survived_the_pipe_delimited_registry(engine):
    desc = engine.TOOL_SPECS["create_cad_part"]["description"]
    assert "create" in desc.lower() or "generate" in desc.lower(), "description was cut short by a stray pipe"


def test_convert_cad_flows_through_to_cad_module(engine, tmp_path, monkeypatch):
    """Prove the tool actually calls cad.convert_cad with what the model sent,
    rather than testing the mock in isolation from the wiring."""
    engine.PERMISSION_MODE = "full-auto"
    engine.ALLOWED_PATHS = [tmp_path]
    src = tmp_path / "part.step"
    src.write_bytes(b"x")

    seen = {}
    def fake_convert(path, fmt, timeout=180):
        seen["path"] = str(path)
        seen["fmt"] = fmt
        return f"Converted {path.name} to part.stl (456 bytes)."
    monkeypatch.setattr(engine.cad, "convert_cad", fake_convert)

    result = _convert_cad(engine, src, "stl")
    assert "Converted part.step to part.stl" in result
    assert seen["fmt"] == "stl"
    assert seen["path"].endswith("part.step")


def test_create_cad_part_flows_through_to_cad_module(engine, tmp_path, monkeypatch):
    """Prove the tool actually calls cad.create_cad_part with what the model sent."""
    engine.PERMISSION_MODE = "full-auto"
    engine.ALLOWED_PATHS = [tmp_path]

    seen = {}
    def fake_create(path, shape, dimensions, timeout=180):
        seen["path"] = str(path)
        seen["shape"] = shape
        seen["dimensions"] = dimensions
        return f"Created {path.name} (789 bytes) — {shape} {dimensions}."
    monkeypatch.setattr(engine.cad, "create_cad_part", fake_create)

    result = _create_cad_part(engine, tmp_path / "out.step", "box", "50x30x10")
    assert "Created out.step" in result
    assert seen["shape"] == "box"
    assert seen["dimensions"] == "50x30x10"
    assert seen["path"].endswith("out.step")


def test_generate_cad_model_flows_through_with_structured_source(engine, tmp_path, monkeypatch):
    engine.PERMISSION_MODE = "full-auto"
    engine.ALLOWED_PATHS = [tmp_path]
    seen = {}

    def fake_generate(path, source, parameters, formats, timeout=600):
        seen.update(path=str(path), source=source, parameters=parameters, formats=formats)
        return "Generated and verified model.step"

    monkeypatch.setattr(engine.cad, "generate_cad_model", fake_generate)
    source = "from build123d import Box\ndef gen_step():\n    return Box(1, 2, 3)"
    result = _generate_cad_model(engine, tmp_path / "model.step", source, '{"x":1}', "step")
    assert "Generated and verified" in result
    assert seen["source"] == source
    assert seen["parameters"] == '{"x":1}'


def test_generate_cad_design_flows_through_as_structured_json(engine, tmp_path, monkeypatch):
    engine.PERMISSION_MODE = "full-auto"
    engine.ALLOWED_PATHS = [tmp_path]
    seen = {}

    def fake_generate(path, design, formats, timeout=600):
        seen.update(path=str(path), design=design, formats=formats)
        return "Generated and verified model.step"

    monkeypatch.setattr(engine.cad, "generate_cad_design", fake_generate)
    design = '{"schema_version":1,"components":[]}'
    result = _generate_cad_design(engine, tmp_path / "model.step", design, "step")
    assert "Generated and verified" in result
    assert seen["design"] == design
    assert seen["formats"] == "step"


def test_validate_cad_model_flows_through_and_parses_false(engine, tmp_path, monkeypatch):
    engine.PERMISSION_MODE = "full-auto"
    engine.ALLOWED_PATHS = [tmp_path]
    model = tmp_path / "model.step"
    model.write_bytes(b"step")
    seen = {}

    def fake_validate(path, render=True, timeout=300):
        seen["render"] = render
        return "Validated model.step"

    monkeypatch.setattr(engine.cad, "validate_cad_model", fake_validate)
    assert "Validated" in _validate_cad_model(engine, model, "false")
    assert seen["render"] is False


def test_open_cad_viewer_flows_through_and_parses_false(engine, tmp_path, monkeypatch):
    engine.PERMISSION_MODE = "readonly"
    engine.ALLOWED_PATHS = [tmp_path]
    model = tmp_path / "model.step"
    model.write_bytes(b"step")
    seen = {}

    def fake_open(path, workspace=None, launch_browser=True, timeout=45):
        seen.update(path=Path(path), workspace=Path(workspace), launch=launch_browser)
        return "CAD Viewer ready: http://127.0.0.1:3245/"

    monkeypatch.setattr(engine.cad, "open_cad_viewer", fake_open)
    result = _open_cad_viewer(engine, model, "false")
    assert result.startswith("CAD Viewer ready")
    assert seen["path"] == model
    assert seen["workspace"] == model.parent
    assert seen["launch"] is False


@pytest.mark.parametrize("mode", ["readonly", "plan-only"])
def test_convert_cad_is_gated_outside_full_auto(engine, tmp_path, mode, monkeypatch):
    monkeypatch.setattr(engine.cad, "convert_cad",
                         lambda *a, **k: pytest.fail("must not run without the write gate"))
    engine.PERMISSION_MODE = mode
    engine.ALLOWED_PATHS = [tmp_path]
    src = tmp_path / "part.step"
    src.write_bytes(b"x")
    out = src.with_suffix(".stl")
    result = _convert_cad(engine, src, "stl")
    # plan-only denies with a different message than readonly's
    # ESCALATION_REQUEST format — the real security property is "no file
    # written", mirroring test_convert_document_tool.py's lenient `or` form.
    assert result.startswith("ESCALATION_REQUEST\x1f") or not out.exists(), (
        "convert_cad wrote a file without passing the write gate")


@pytest.mark.parametrize("mode", ["readonly", "plan-only"])
def test_create_cad_part_is_gated_outside_full_auto(engine, tmp_path, mode, monkeypatch):
    monkeypatch.setattr(engine.cad, "create_cad_part",
                         lambda *a, **k: pytest.fail("must not run without the write gate"))
    engine.PERMISSION_MODE = mode
    engine.ALLOWED_PATHS = [tmp_path]
    out = tmp_path / "out.step"
    result = _create_cad_part(engine, out, "box", "50x30x10")
    # Same lenient check: no file written is the security property.
    assert result.startswith("ESCALATION_REQUEST\x1f") or not out.exists(), (
        "create_cad_part wrote a file without passing the write gate")


def test_convert_cad_cannot_target_a_sensitive_path(engine, tmp_path, monkeypatch):
    """The credential floor is unconditional; a CAD conversion must not dodge it
    just because the tool's job is conversion, not creation."""
    monkeypatch.setattr(engine.cad, "convert_cad",
                         lambda *a, **k: pytest.fail("must not reach cad.py for a sensitive path"))
    engine.PERMISSION_MODE = "full-auto"
    engine.ALLOWED_PATHS = [tmp_path]
    target = tmp_path / "id_rsa"
    target.write_bytes(b"x")
    result = _convert_cad(engine, target, "stl")
    assert "Error" in result or "denied" in result.lower()


def test_create_cad_part_cannot_target_a_sensitive_path(engine, tmp_path, monkeypatch):
    """The credential floor is unconditional; the output path must not be a
    sensitive file."""
    monkeypatch.setattr(engine.cad, "create_cad_part",
                         lambda *a, **k: pytest.fail("must not reach cad.py for a sensitive path"))
    engine.PERMISSION_MODE = "full-auto"
    engine.ALLOWED_PATHS = [tmp_path]
    target = tmp_path / "id_rsa"
    result = _create_cad_part(engine, target, "box", "50x30x10")
    assert "Error" in result or "denied" in result.lower()


@pytest.mark.parametrize("tool", ["generate_cad_design", "generate_cad_model", "validate_cad_model"])
def test_advanced_cad_tools_are_gated_outside_full_auto(engine, tmp_path, monkeypatch, tool):
    engine.PERMISSION_MODE = "readonly"
    engine.ALLOWED_PATHS = [tmp_path]
    target = tmp_path / "model.step"
    target.write_bytes(b"step")
    monkeypatch.setattr(
        engine.cad, tool,
        lambda *a, **k: pytest.fail("CAD backend must not run before the write gate"),
    )
    if tool == "generate_cad_design":
        result = _generate_cad_design(engine, target, '{"components":[]}')
    elif tool == "generate_cad_model":
        result = _generate_cad_model(engine, target, "def gen_step():\n    pass")
    else:
        result = _validate_cad_model(engine, target)
    assert result.startswith("ESCALATION_REQUEST\x1f") or "denied" in result.lower()
# The CAD contract is injected into the system prompt while the same round
# filters the tool schema. Those two must agree: the first version named
# generate_cad_model unconditionally, so a request with no advanced-geometry
# keyword hid the tool, the model followed the prompt anyway, and the call came
# back "Unknown tool 'generate_cad_model' - not available."
GENERATION_TOOLS = ("generate_cad_design", "generate_cad_model")


@pytest.mark.parametrize("available", [
    {"create_cad_part", "generate_cad_design", "generate_cad_model"},
    {"create_cad_part", "generate_cad_design"},
    {"create_cad_part"},
    set(),
])
def test_cad_contract_never_names_an_unavailable_generation_tool(engine, available):
    contract = engine._cad_runtime_instruction(available)
    for tool in GENERATION_TOOLS:
        if tool in available:
            continue
        directive = [
            line for line in contract.splitlines()
            if tool in line and "NOT available" not in line and "Do not call" not in line
        ]
        assert not directive, (
            f"contract directs the model to {tool}, which is not in the round's schema: "
            f"{directive}"
        )


def test_cad_contract_directs_to_the_declarative_tool_when_it_is_the_only_one(engine):
    contract = engine._cad_runtime_instruction({"create_cad_part", "generate_cad_design"})
    assert "use generate_cad_design" in contract
    assert "generate_cad_model is NOT available" in contract


def test_cad_contract_stops_generation_talk_once_the_retry_budget_is_gone(engine):
    contract = engine._cad_runtime_instruction({"create_cad_part"})
    assert "No CAD generation tool is available" in contract
