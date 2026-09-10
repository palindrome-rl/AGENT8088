"""Vision-capability resolution: normalized matching, precedence, and source.

The catalog answers an exact provider:model key and nothing else, so a model
named with a version suffix the curator did not anticipate
("gemini-2.5-flash-002") reads as text-only and silently gets OCR. These tests
pin the wider resolution chain: exact -> normalized -> family -> live probe ->
False, and the `source` each answer came from so a caller can say so out loud.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest
from agent8088 import model_catalog


@pytest.fixture(autouse=True)
def _reset_cache():
    model_catalog.reload_catalog()
    yield
    model_catalog.reload_catalog()


class _Client:
    """Stand-in for the OpenAI client: the probe only reads these two."""

    def __init__(self, base_url="http://127.0.0.2:8080/v1", api_key="k"):
        self.base_url = base_url
        self.api_key = api_key


def _user_catalog(tmp_path, monkeypatch, entries):
    path = tmp_path / "model_catalog.json"
    path.write_text(json.dumps(entries), encoding="utf-8")
    monkeypatch.setattr(model_catalog, "_user_catalog_path", lambda: path)
    model_catalog.reload_catalog()


# --- exact match, the behaviour that already worked ------------------------

def test_exact_bundled_match_reports_the_bundled_source():
    decision = model_catalog.vision_decision("openai", "gpt-4o")
    assert decision.vision is True
    assert decision.source == "bundled"


# --- normalized match ------------------------------------------------------

def test_version_suffix_falls_back_to_the_model_family():
    # gemini:gemini-2.5-flash is a real bundled vision entry; the -002 build
    # of it is the same model and must not read as text-only.
    decision = model_catalog.vision_decision("gemini", "gemini-2.5-flash-002")
    assert decision.vision is True
    assert decision.source == "family"


def test_long_preview_suffix_falls_back_to_the_model_family():
    decision = model_catalog.vision_decision("gemini", "gemini-2.5-pro-preview-06-2026")
    assert decision.vision is True
    assert decision.source == "family"


def test_free_tag_is_normalized_off_the_catalog_key():
    # The bundled key is "cohere/north-mini-code:free"; the same model
    # requested without the tag is the same model.
    decision = model_catalog.vision_decision("openrouter", "cohere/north-mini-code")
    assert decision.vision is False
    assert decision.source == "normalized"


def test_vendor_path_is_stripped_when_the_catalog_omits_it(tmp_path, monkeypatch):
    _user_catalog(tmp_path, monkeypatch,
                  {"openrouter:some-vision-model": {"rank": 50, "vision": True, "tools": True}})
    decision = model_catalog.vision_decision("openrouter", "vendorx/some-vision-model")
    assert decision.vision is True
    assert decision.source == "normalized"


def test_family_fallback_never_strips_down_to_one_segment(tmp_path, monkeypatch):
    # "openai:gpt" would match almost every OpenAI model if the walk were
    # allowed to run that far, so it must not be reached at all.
    _user_catalog(tmp_path, monkeypatch,
                  {"openai:gpt": {"rank": 50, "vision": True, "tools": True}})
    decision = model_catalog.vision_decision("openai", "gpt-9-imaginary-build")
    assert decision.vision is False
    assert decision.source == "default"


def test_a_family_match_does_not_cross_providers():
    # gemini:gemini-2.5-flash exists; the same model name under a provider
    # that does not have it must not borrow the entry.
    decision = model_catalog.vision_decision("cerebras", "gemini-2.5-flash-002")
    assert decision.vision is False
    assert decision.source == "default"


# --- the live probe --------------------------------------------------------

def test_an_exact_catalog_hit_never_calls_the_probe(monkeypatch):
    calls = []

    def _probe(*args, **kwargs):
        calls.append(args)
        return False

    monkeypatch.setattr("agent8088.providers.probe_model_vision", _probe)
    decision = model_catalog.vision_decision("openai", "gpt-4o", client=_Client())
    assert decision.vision is True
    assert decision.source == "bundled"
    assert calls == []


def test_an_uncatalogued_model_is_probed(monkeypatch):
    monkeypatch.setattr("agent8088.providers.probe_model_vision",
                        lambda *a, **k: True)
    decision = model_catalog.vision_decision(
        "rtx3090", "qwen3.8-27b-rtx3090-long", client=_Client())
    assert decision.vision is True
    assert decision.source == "probe"


def test_a_probe_that_definitively_says_no_is_reported_as_the_probe(monkeypatch):
    monkeypatch.setattr("agent8088.providers.probe_model_vision",
                        lambda *a, **k: False)
    decision = model_catalog.vision_decision(
        "rtx3090", "qwen3.8-27b-rtx3090-long", client=_Client())
    assert decision.vision is False
    assert decision.source == "probe"


def test_an_inconclusive_probe_falls_through_to_the_default(monkeypatch):
    monkeypatch.setattr("agent8088.providers.probe_model_vision",
                        lambda *a, **k: None)
    decision = model_catalog.vision_decision(
        "rtx3090", "qwen3.8-27b-rtx3090-long", client=_Client())
    assert decision.vision is False
    assert decision.source == "default"


def test_a_user_entry_outranks_the_probe(monkeypatch, tmp_path):
    _user_catalog(tmp_path, monkeypatch,
                  {"rtx3090:qwen3.8-27b-rtx3090-long": {"rank": 50, "vision": True, "tools": True}})
    monkeypatch.setattr("agent8088.providers.probe_model_vision",
                        lambda *a, **k: False)
    decision = model_catalog.vision_decision(
        "rtx3090", "qwen3.8-27b-rtx3090-long", client=_Client())
    assert decision.vision is True
    assert decision.source == "user"


def test_a_raising_probe_does_not_break_the_decision(monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError("endpoint on fire")

    monkeypatch.setattr("agent8088.providers.probe_model_vision", _boom)
    decision = model_catalog.vision_decision(
        "rtx3090", "qwen3.8-27b-rtx3090-long", client=_Client())
    assert decision.vision is False
    assert decision.source == "default"


def test_no_client_means_no_probe(monkeypatch):
    calls = []
    monkeypatch.setattr("agent8088.providers.probe_model_vision",
                        lambda *a, **k: calls.append(a) or True)
    decision = model_catalog.vision_decision("rtx3090", "qwen3.8-27b-rtx3090-long")
    assert decision.vision is False
    assert decision.source == "default"
    assert calls == []


# --- the bool wrapper the existing callers use -----------------------------

def test_vision_capable_keeps_its_two_argument_signature():
    assert model_catalog.vision_capable("openai", "gpt-4o") is True
    assert model_catalog.vision_capable("deepseek", "deepseek-chat") is False


def test_vision_capable_accepts_a_client_and_probes(monkeypatch):
    monkeypatch.setattr("agent8088.providers.probe_model_vision",
                        lambda *a, **k: True)
    assert model_catalog.vision_capable(
        "rtx3090", "qwen3.8-27b-rtx3090-long", client=_Client()) is True
