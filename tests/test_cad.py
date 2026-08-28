"""cad.convert_cad and cad.create_cad_part — the FreeCAD conversion path.

Mirrors documents.convert_document's pattern: subprocess.run is monkeypatched
throughout — these tests never need a real FreeCAD install, and must still pass
in CI/dev environments without one. Every conversion path is verified against the
actual artifact on disk rather than trusting exit code or stdout, because FreeCAD
may exit 0 even after a script exception (unverified against a real install, see
the cad.py module docstring).
"""
import subprocess


from agent8088 import cad


def test_convert_cad_unsupported_target_format_is_refused_before_touching_freecad(monkeypatch, tmp_path):
    monkeypatch.setattr(cad, "freecad_executable", lambda: None)  # would fail loudly if reached
    src = tmp_path / "part.step"
    src.write_bytes(b"x")
    result = cad.convert_cad(src, "pdf")
    assert "pdf" in result
    assert "Supported targets" in result


def test_convert_cad_missing_freecad_gives_an_actionable_message(monkeypatch, tmp_path):
    monkeypatch.setattr(cad, "freecad_executable", lambda: None)
    src = tmp_path / "part.step"
    src.write_bytes(b"x")
    result = cad.convert_cad(src, "stl")
    assert "not installed" in result
    assert "winget install FreeCAD.FreeCAD" in result


def test_convert_cad_missing_source_file_is_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(cad, "freecad_executable", lambda: "freecadcmd")
    result = cad.convert_cad(tmp_path / "nope.step", "stl")
    assert "does not exist" in result


def test_convert_cad_successful_conversion_reports_the_output_file(monkeypatch, tmp_path):
    src = tmp_path / "part.step"
    src.write_bytes(b"x")
    monkeypatch.setattr(cad, "freecad_executable", lambda: "freecadcmd")

    def fake_run(argv, **kwargs):
        # freecadcmd script produces output file in same dir with new extension,
        # simulate that side effect so the disk-check in convert_cad sees it.
        (tmp_path / "part.stl").write_bytes(b"fake stl data")
        return subprocess.CompletedProcess(argv, 0, stdout="export ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = cad.convert_cad(src, "stl")
    assert "Converted part.step to part.stl" in result
    assert "bytes" in result


def test_convert_cad_freecad_runs_but_produces_nothing_is_a_failure_not_a_silent_success(monkeypatch, tmp_path):
    """freecadcmd may exit 0 on a script exception (unverified, see module
    docstring) — the disk state is the only source of truth, not stdout text
    or exit code."""
    src = tmp_path / "part.step"
    src.write_bytes(b"x")
    monkeypatch.setattr(cad, "freecad_executable", lambda: "freecadcmd")
    monkeypatch.setattr(
        subprocess, "run",
        lambda argv, **kw: subprocess.CompletedProcess(argv, 0, stdout="", stderr="export failed"),
    )
    result = cad.convert_cad(src, "stl")
    assert "Conversion failed" in result
    assert "export failed" in result


def test_convert_cad_timeout_gives_a_clear_message_not_a_traceback(monkeypatch, tmp_path):
    src = tmp_path / "part.step"
    src.write_bytes(b"x")
    monkeypatch.setattr(cad, "freecad_executable", lambda: "freecadcmd")

    def fake_run(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs.get("timeout"))

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = cad.convert_cad(src, "stl", timeout=10)
    assert "timed out" in result


def test_convert_cad_target_format_accepts_a_leading_dot_and_mixed_case(monkeypatch, tmp_path):
    """Model-supplied args are free text — '.STL' and 'stl' should behave
    identically rather than one silently failing validation."""
    src = tmp_path / "part.step"
    src.write_bytes(b"x")
    monkeypatch.setattr(cad, "freecad_executable", lambda: None)  # fails past validation, not on it
    result = cad.convert_cad(src, ".STL")
    assert "Supported targets" not in result  # validation passed
    assert "not installed" in result  # reached the freecad-missing branch


def test_create_cad_part_invalid_shape_is_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(cad, "freecad_executable", lambda: None)  # would fail loudly if reached
    result = cad.create_cad_part(tmp_path / "out.step", "torus", "10x5")
    assert "Unknown shape" in result


def test_create_cad_part_invalid_output_format_is_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(cad, "freecad_executable", lambda: None)  # would fail loudly if reached
    result = cad.create_cad_part(tmp_path / "out.docx", "box", "50x30x10")
    assert "Supported output formats" in result


def test_create_cad_part_dimension_parsing_shorthand_form(monkeypatch, tmp_path):
    """Dimension parsing works for the shorthand 'NxNxN' form."""
    monkeypatch.setattr(cad, "freecad_executable", lambda: "freecadcmd")

    def fake_run(argv, **kwargs):
        # Extract output path from the script — it's embedded in the FreeCAD Python code
        (tmp_path / "out.step").write_bytes(b"fake step data")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = cad.create_cad_part(tmp_path / "out.step", "box", "50x30x10")
    # Should not be a dimension parse error
    assert "Expected e.g." not in result
    assert "is not a number" not in result


def test_create_cad_part_dimension_parsing_key_value_form(monkeypatch, tmp_path):
    """Dimension parsing works for the key=value form."""
    monkeypatch.setattr(cad, "freecad_executable", lambda: "freecadcmd")

    def fake_run(argv, **kwargs):
        (tmp_path / "out.step").write_bytes(b"fake step data")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = cad.create_cad_part(tmp_path / "out.step", "cylinder", "radius=10,height=50")
    # Should not be a dimension parse error
    assert "Expected e.g." not in result
    assert "is not a number" not in result


def test_create_cad_part_malformed_dimension_string_returns_string_never_raises(monkeypatch, tmp_path):
    """Malformed dimension strings must return a plain-language error, never raise."""
    monkeypatch.setattr(cad, "freecad_executable", lambda: "freecadcmd")

    # Too few values for the shape
    result = cad.create_cad_part(tmp_path / "out.step", "box", "50x30")
    assert isinstance(result, str)
    assert "has 2 value(s)" in result or "needs 3" in result

    # Non-numeric value
    result = cad.create_cad_part(tmp_path / "out.step", "box", "50xNOTANUMBERx10")
    assert isinstance(result, str)
    assert "is not a number" in result


def test_create_cad_part_missing_freecad_gives_an_actionable_message(monkeypatch, tmp_path):
    monkeypatch.setattr(cad, "freecad_executable", lambda: None)
    result = cad.create_cad_part(tmp_path / "out.step", "box", "50x30x10")
    assert "not installed" in result
    assert "winget install FreeCAD.FreeCAD" in result


def test_create_cad_part_successful_generation_reports_the_output_file(monkeypatch, tmp_path):
    monkeypatch.setattr(cad, "freecad_executable", lambda: "freecadcmd")

    def fake_run(argv, **kwargs):
        # freecadcmd script creates the output file in the specified path
        (tmp_path / "out.step").write_bytes(b"fake step data")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = cad.create_cad_part(tmp_path / "out.step", "box", "50x30x10")
    assert "Created out.step" in result
    assert "bytes" in result


def test_create_cad_part_freecad_runs_but_produces_nothing_is_a_failure_not_a_silent_success(monkeypatch, tmp_path):
    """freecadcmd may exit 0 on a script exception — the disk state is the only
    source of truth."""
    monkeypatch.setattr(cad, "freecad_executable", lambda: "freecadcmd")
    monkeypatch.setattr(
        subprocess, "run",
        lambda argv, **kw: subprocess.CompletedProcess(argv, 0, stdout="", stderr="export error"),
    )
    result = cad.create_cad_part(tmp_path / "out.step", "box", "50x30x10")
    assert "Generation failed" in result
    assert "export error" in result


def test_create_cad_part_timeout_gives_a_clear_message_not_a_traceback(monkeypatch, tmp_path):
    monkeypatch.setattr(cad, "freecad_executable", lambda: "freecadcmd")

    def fake_run(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs.get("timeout"))

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = cad.create_cad_part(tmp_path / "out.step", "box", "50x30x10", timeout=10)
    assert "timed out" in result


def test_extract_info_returns_none_for_non_cad_extension(tmp_path):
    """extract_info returns None for extensions it doesn't handle, so the caller
    falls through to normal reading."""
    txt_path = tmp_path / "data.txt"
    txt_path.write_text("plain text")
    result = cad.extract_info(txt_path)
    assert result is None
