# tests/test_model_catalog_data.py
import json
from pathlib import Path

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
