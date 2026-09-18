"""The bundled catalog's answer for a model that can see.

`/image` on ollama-cloud:glm-5.3-flash came back
`error: OCR is not installed` — on a model the endpoint itself reports as
vision-capable. Every one of the 21 bundled `ollama-cloud:` entries said
`vision: false`, and an exact bundled entry short-circuits `vision_decision`
before the endpoint is ever asked, so the probe that would have corrected it
never ran.

Eight were wrong, confirmed against Ollama's own `/api/show` for each id:

    gemma4:31b  glm-5.3-flash  kimi-k2.6  kimi-k2.7-code
    kimi-k3     minimax-m3     mistral-large-3:675b  qwen3.5:397b

This is a data test on purpose. Probing live would be the more direct check and
would also be a network call per model in the unit suite, and flaky the first
time an id is retired.
"""

import json
from pathlib import Path

import pytest

CATALOG = Path(__file__).resolve().parents[1] / "src" / "agent8088" / "model_catalog.json"

# Confirmed vision-capable by the endpoint, 2026-09-18.
SEEING = (
    "gemma4:31b",
    "glm-5.3-flash",
    "kimi-k2.6",
    "kimi-k2.7-code",
    "kimi-k3",
    "minimax-m3",
    "mistral-large-3:675b",
    "qwen3.5:397b",
)

# Confirmed text-only by the same endpoint. Here so a blanket "mark everything
# vision" fix fails too — the catalog has to be right, not merely different.
BLIND = (
    "glm-5.3",
    "gpt-oss:120b",
    "deepseek-v4-pro:0813",
    "nemotron-3-super",
    "minimax-m2.7",
)


@pytest.fixture(scope="module")
def catalog():
    return json.loads(CATALOG.read_text(encoding="utf-8"))


@pytest.mark.parametrize("model", SEEING)
def test_a_model_that_can_see_is_catalogued_as_seeing(catalog, model):
    entry = catalog.get(f"ollama-cloud:{model}")

    assert entry is not None, f"{model} is missing from the catalog"
    assert entry["vision"] is True, (
        f"{model} can read images; catalogued blind, /image silently sends it OCR text")


@pytest.mark.parametrize("model", BLIND)
def test_a_text_only_model_is_still_catalogued_blind(catalog, model):
    entry = catalog["ollama-cloud:" + model]

    assert entry["vision"] is False, (
        f"{model} cannot read images; image parts get it an error, not an answer")


def test_the_provider_is_not_uniformly_one_answer(catalog):
    """What made the bug invisible: every entry agreed, so nothing looked odd."""
    values = {entry["vision"]
              for key, entry in catalog.items() if key.startswith("ollama-cloud:")}

    assert values == {True, False}, "every ollama-cloud entry has the same verdict again"


def test_vision_decision_prefers_the_bundled_answer_over_a_probe(engine):
    """The short circuit itself is correct and stays — 145 known pairs resolve
    offline. The fix is that the data it short-circuits to is now right.
    """
    from agent8088 import model_catalog

    calls = []

    class Probed:
        def __getattr__(self, name):
            calls.append(name)
            raise AssertionError("the endpoint was asked about a catalogued model")

    decision = model_catalog.vision_decision(
        "ollama-cloud", "glm-5.3-flash", client=Probed())

    assert decision.vision is True
    assert decision.source == "bundled"
    assert calls == []
