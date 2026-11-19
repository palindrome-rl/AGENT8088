"""`view_image` lets the agent look at an image file on disk, for any session.

It was registered only in the benchmark profile. For everyone it reads local files inside the
allowed paths only (a URL is refused unless `image_tool_allow_urls` is on), counts as a read in
readonly and plan mode, and is offered when a request is about looking at a picture.
"""
import json

import pytest
from PIL import Image


@pytest.fixture
def workspace(engine, monkeypatch, tmp_path):
    monkeypatch.setattr(engine, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(engine, "ALLOWED_PATHS", [tmp_path])
    monkeypatch.setattr(engine, "BLOCKED_PATHS", [])
    return tmp_path


def _png(path, size=(80, 60)):
    Image.new("RGB", size, (30, 120, 200)).save(path, "PNG")
    return path


def test_view_image_is_a_core_tool(engine):
    spec = engine.TOOL_SPECS.get("view_image")
    assert spec is not None, "view_image is registered in src/agent8088/tools.txt"
    assert spec["mode"] == "image"
    assert engine.required_params(spec) == ["path"]


def test_a_local_image_is_attached(engine, workspace):
    _png(workspace / "house.png")
    result = engine.run_tool("view_image", {"path": "house.png"})
    assert result.startswith(engine._IMAGE_MARKER_PREFIX)
    message = json.loads(result[len(engine._IMAGE_MARKER_PREFIX):])["message"]
    assert any(part.get("type") == "image_url" for part in message["content"])


def test_a_url_is_refused_by_default(engine, workspace):
    result = engine.run_tool("view_image", {"path": "https://example.com/picture.png"})
    assert not result.startswith(engine._IMAGE_MARKER_PREFIX)
    assert "local image file" in result


def test_urls_can_be_allowed_by_config(engine, workspace, monkeypatch):
    monkeypatch.setattr(engine, "IMAGE_TOOL_ALLOW_URLS", True)
    monkeypatch.setattr(engine, "_egress_check", lambda url: None)
    monkeypatch.setattr(engine, "_ssrf_check", lambda url: None)
    result = engine.run_tool("view_image", {"path": "https://example.com/picture.png"})
    assert result.startswith(engine._IMAGE_MARKER_PREFIX)


def test_a_path_outside_the_project_is_refused(engine, workspace, tmp_path_factory):
    outside = _png(tmp_path_factory.mktemp("elsewhere") / "logo.png")
    result = engine.run_tool("view_image", {"path": str(outside)})
    assert not result.startswith(engine._IMAGE_MARKER_PREFIX)


def test_an_oversized_image_is_refused(engine, workspace, monkeypatch):
    _png(workspace / "big.png", (400, 400))
    monkeypatch.setattr(engine, "MAX_IMAGE_BYTES", 100)
    result = engine.run_tool("view_image", {"path": "big.png"})
    assert not result.startswith(engine._IMAGE_MARKER_PREFIX)
    assert "too large" in result


def test_a_non_image_file_is_refused(engine, workspace):
    (workspace / "notes.txt").write_text("hello", encoding="utf-8")
    result = engine.run_tool("view_image", {"path": "notes.txt"})
    assert not result.startswith(engine._IMAGE_MARKER_PREFIX)
    assert "Unsupported image type" in result


def test_view_image_works_in_readonly_mode(engine, workspace, monkeypatch):
    monkeypatch.setattr(engine, "PERMISSION_MODE", "readonly")
    _png(workspace / "chart.png")
    assert engine.run_tool("view_image", {"path": "chart.png"}).startswith(engine._IMAGE_MARKER_PREFIX)


def test_view_image_is_offered_in_plan_mode(engine, monkeypatch):
    from agent8088 import cli
    monkeypatch.setattr(cli.A, "PERMISSION_MODE", "plan-only")
    assert "view_image" in cli._active_tool_specs()


@pytest.mark.parametrize("request_text", [
    "Look at diagram.png and tell me what is wrong with the layout.",
    "Here is a screenshot of the signup form, does it match the mockup?",
    "Check the picture in assets/logo.png and describe its colours.",
    "What does the photo at docs/team.jpg show?",
])
def test_a_request_about_a_picture_is_offered_view_image(engine, monkeypatch, request_text):
    monkeypatch.setattr(engine.memory, "embedder", lambda: None)
    engine._TOOL_INDEX_CACHE.clear()
    chosen = engine.select_tool_names_for_request(request_text, set(engine.TOOL_SPECS), mode="hybrid")
    assert "view_image" in chosen


@pytest.mark.parametrize("request_text", [
    "Show me my current uncommitted git diff.",
    "Write a function that sorts a list of dates.",
    "Summarise the README for me.",
])
def test_other_requests_are_not_given_view_image_by_the_guard(engine, request_text):
    guarded = engine._apply_tool_intent_guards(request_text, set(), set(engine.TOOL_SPECS))
    assert "view_image" not in guarded
