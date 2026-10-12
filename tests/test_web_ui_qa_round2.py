"""The Artifacts listing, and resume clearing the last-turn numbers."""

import asyncio
import json
from types import SimpleNamespace

from agent8088 import web_server as web


def _artifacts(monkeypatch, root):
    monkeypatch.setattr(web, "_eng", lambda: SimpleNamespace(ARTIFACTS_ROOT=root))


def test_a_missing_artifacts_folder_lists_as_empty(monkeypatch, tmp_path):
    _artifacts(monkeypatch, tmp_path / "artifacts")
    listing = asyncio.run(web.list_artifacts(""))
    assert listing["items"] == [] and listing["cwd"] == ""


def test_a_missing_subfolder_is_still_not_found(monkeypatch, tmp_path):
    (tmp_path / "artifacts").mkdir()
    _artifacts(monkeypatch, tmp_path / "artifacts")
    response = asyncio.run(web.list_artifacts("nope"))
    assert response.status_code == 404


def test_nested_paths_use_forward_slashes(monkeypatch, tmp_path):
    root = tmp_path / "artifacts"
    (root / "sub" / "deeper").mkdir(parents=True)
    (root / "sub" / "deeper" / "a.txt").write_text("x", encoding="utf-8")
    _artifacts(monkeypatch, root)

    listing = asyncio.run(web.list_artifacts("sub"))
    assert [item["path"] for item in listing["items"]] == ["sub/deeper"]
    deeper = asyncio.run(web.list_artifacts("sub/deeper"))
    assert deeper["items"][0]["path"] == "sub/deeper/a.txt"
    assert deeper["parent"] == "sub"


def test_resume_clears_the_previous_sessions_last_turn(monkeypatch, tmp_path):
    (tmp_path / "saved.json").write_text(json.dumps({"messages": []}), encoding="utf-8")
    fake = SimpleNamespace(
        S=SimpleNamespace(max_turns=50, name="", last_usage={"seconds": 85.6, "tokens": 615}),
        _save_active_session=lambda: None,
        _session_name=lambda name: name, _session_path=lambda name: tmp_path / f"{name}.json")
    monkeypatch.setattr(web, "_cl", lambda: fake)

    asyncio.run(web.resume_session(web.SessionActionBody(name="saved")))
    assert fake.S.last_usage is None
