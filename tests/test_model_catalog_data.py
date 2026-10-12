# tests/test_model_catalog_data.py
import json
from pathlib import Path

import pytest

CATALOG_PATH = (Path(__file__).resolve().parent.parent
                / "src" / "agent8088" / "model_catalog.json")


def test_bundled_catalog_is_valid_and_well_shaped():
    data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert len(data) > 0
    for key, entry in data.items():
        assert ":" in key, f"key {key!r} must be provider:model"
        assert isinstance(entry, dict)
        assert isinstance(entry["rank"], int) and 0 <= entry["rank"] <= 100
        assert isinstance(entry["vision"], bool)
        assert isinstance(entry["tools"], bool)


def test_bundled_catalog_covers_every_builtin_default_model():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    from agent8088.providers import BUILTIN_PROVIDERS

    data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    missing = [
        f"{name}:{info['default_model']}"
        for name, info in BUILTIN_PROVIDERS.items()
        if f"{name}:{info['default_model']}" not in data
    ]
    assert not missing, f"catalog is missing default models: {missing}"


@pytest.mark.parametrize("key", [
    "mistral:mistral-ocr-latest", "mistral:mistral-ocr-2512",
    "mistral:mistral-ocr-4-1", "mistral:codestral-embed",
    "mistral:codestral-embed-2505", "mistral:mistral-embed",
    "mistral:mistral-code-fim-latest", "mistral:mistral-moderation-2603",
    "mistral:voxtral-mini-tts-latest", "mistral:voxtral-mini-realtime-latest",
    "gemini:gemini-embedding-001", "gemini:gemini-embedding-2-preview",
    "gemini:gemini-2.5-pro-preview-tts", "gemini:gemini-3.5-transcribe",
    "gemini:veo-3.1-generate-preview", "gemini:lyria-3-pro-preview",
    "openrouter:google/lyria-3-pro-preview",
    "groq:canopylabs/orpheus-v1-english", "groq:canopylabs/orpheus-arabic-saudi",
])
def test_dedicated_endpoints_are_not_agent_chat_capabilities(key, monkeypatch, tmp_path):
    from agent8088 import model_catalog

    data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    assert data[key] == {"rank": 0, "vision": False, "tools": False}
    # Accepting images in a dedicated API is not vision CHAT support.
    with monkeypatch.context() as patch:
        patch.setattr(model_catalog, "_user_catalog_path", lambda: tmp_path / "absent.json")
        patch.setattr(model_catalog, "_probe", lambda *args: pytest.fail("must remain offline"))
        model_catalog.reload_catalog()
        try:
            provider, model = key.split(":", 1)
            decision = model_catalog.vision_decision(provider, model, client=object())
            assert decision == model_catalog.VisionDecision(False, "bundled")
        finally:
            model_catalog._catalog_cache = None
            model_catalog._user_cache = None
            model_catalog._normalized_cache = None
