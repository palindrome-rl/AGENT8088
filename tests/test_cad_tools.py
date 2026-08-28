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

import pytest


def _convert_cad(engine, path, fmt):
    return engine.exec_tool(
        "convert_cad", json.dumps({"filename": str(path), "format": fmt}))


def _create_cad_part(engine, path, shape, dimensions):
    return engine.exec_tool(
        "create_cad_part", json.dumps({"filename": str(path), "shape": shape, "dimensions": dimensions}))


@pytest.fixture
def ready(engine, tmp_path):
    engine.PERMISSION_MODE = "full-auto"
    engine.ALLOWED_PATHS = [tmp_path]
    return engine


def test_convert_cad_is_registered_with_the_write_mode(engine):
    assert engine.TOOL_SPECS["convert_cad"]["mode"] == "write_text"


def test_create_cad_part_is_registered_with_the_write_mode(engine):
    assert engine.TOOL_SPECS["create_cad_part"]["mode"] == "write_text"


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
