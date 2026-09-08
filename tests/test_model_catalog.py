import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest
from agent8088 import model_catalog


@pytest.fixture(autouse=True)
def _reset_cache():
    """Every test gets a clean cache and clean path overrides."""
    model_catalog.reload_catalog()
    yield
    model_catalog.reload_catalog()


def test_lookup_hits_the_bundled_catalog():
    # gemini:gemini-2.5-pro is a real bundled entry (Task 1).
    entry = model_catalog.lookup("gemini", "gemini-2.5-pro")
    assert entry is not None
    assert entry["rank"] == 85
    assert entry["vision"] is True


def test_lookup_returns_none_for_an_unknown_pair():
    assert model_catalog.lookup("nosuchprovider", "nosuchmodel") is None


def test_user_catalog_overrides_bundled_on_collision(tmp_path, monkeypatch):
    user_file = tmp_path / "model_catalog.json"
    user_file.write_text(json.dumps({
        "gemini:gemini-2.5-pro": {"rank": 10, "vision": False, "tools": False},
    }), encoding="utf-8")
    monkeypatch.setattr(model_catalog, "_user_catalog_path", lambda: user_file)
    model_catalog.reload_catalog()

    entry = model_catalog.lookup("gemini", "gemini-2.5-pro")
    assert entry == {"rank": 10, "vision": False, "tools": False}


def test_user_catalog_adds_new_entries_without_losing_bundled_ones(tmp_path, monkeypatch):
    user_file = tmp_path / "model_catalog.json"
    user_file.write_text(json.dumps({
        "customprovider:my-model": {"rank": 99, "vision": False, "tools": True},
    }), encoding="utf-8")
    monkeypatch.setattr(model_catalog, "_user_catalog_path", lambda: user_file)
    model_catalog.reload_catalog()

    assert model_catalog.lookup("customprovider", "my-model")["rank"] == 99
    # bundled entries must still be there
    assert model_catalog.lookup("gemini", "gemini-2.5-pro") is not None


def test_missing_user_catalog_file_is_treated_as_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(model_catalog, "_user_catalog_path",
                        lambda: tmp_path / "does_not_exist.json")
    model_catalog.reload_catalog()
    # must not raise, and bundled data must still load
    assert model_catalog.lookup("gemini", "gemini-2.5-pro") is not None


def test_malformed_user_catalog_file_does_not_crash(tmp_path, monkeypatch):
    user_file = tmp_path / "model_catalog.json"
    user_file.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setattr(model_catalog, "_user_catalog_path", lambda: user_file)
    model_catalog.reload_catalog()  # must not raise
    assert model_catalog.lookup("gemini", "gemini-2.5-pro") is not None


def test_load_catalog_is_cached_until_reload(tmp_path, monkeypatch):
    user_file = tmp_path / "model_catalog.json"
    user_file.write_text(json.dumps({}), encoding="utf-8")
    monkeypatch.setattr(model_catalog, "_user_catalog_path", lambda: user_file)
    model_catalog.reload_catalog()
    first = model_catalog.load_catalog()

    user_file.write_text(json.dumps({
        "gemini:gemini-2.5-pro": {"rank": 1, "vision": False, "tools": False},
    }), encoding="utf-8")
    # without reload_catalog(), the cached value is unchanged
    assert model_catalog.load_catalog() is first
    assert model_catalog.lookup("gemini", "gemini-2.5-pro")["rank"] == 85

    model_catalog.reload_catalog()
    assert model_catalog.lookup("gemini", "gemini-2.5-pro")["rank"] == 1
