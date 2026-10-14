import json
import pytest

SECRET = "fake-regression-credential-123456"


@pytest.fixture
def workspace(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(engine, "ARTIFACTS_ROOT", tmp_path / "artifacts")
    monkeypatch.setattr(engine, "ALLOWED_PATHS", [tmp_path])
    monkeypatch.setattr(engine, "PERMISSION_MODE", "full-auto")
    monkeypatch.setattr(engine, "PLAN_AUDIT", False)
    monkeypatch.setattr(engine, "BLOCK_SECRETS_IN_WRITES", True)
    monkeypatch.setattr(engine, "_SECRET_VALUES", {SECRET, "short"})
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    engine.reset_turn_counters()
    return engine, repo


def write(engine, target, content, **extra):
    return engine.exec_tool("write_file", json.dumps({"filename": str(target), "content": content, **extra}))


def test_new_file_secret_is_refused_without_echo_or_budget_spend(workspace):
    engine, repo = workspace
    result = write(engine, repo / "app.txt", SECRET)
    assert "credential" in result and SECRET not in result
    assert not (repo / "app.txt").exists() and engine._turn_writes == 0


def test_append_part_with_secret_leaves_original_intact(workspace):
    engine, repo = workspace
    path = repo / "app.txt"
    path.write_text("original", encoding="utf-8")
    assert "credential" in write(engine, path, SECRET, mode="append")
    assert path.read_text(encoding="utf-8") == "original"


def test_append_boundary_cannot_assemble_secret(workspace):
    engine, repo = workspace
    path = repo / "app.txt"
    path.write_text(SECRET[:-8], encoding="utf-8")
    result = write(engine, path, SECRET[-8:], mode="append")
    assert "credential" in result and SECRET not in result
    assert path.read_text(encoding="utf-8") == SECRET[:-8]


def test_multiple_parts_cannot_assemble_secret(workspace):
    engine, repo = workspace
    path = repo / "app.txt"
    path.touch()
    assert write(engine, path, SECRET[:10]).startswith("Wrote ")
    assert write(engine, path, SECRET[10:20], mode="append").startswith("Appended ")
    assert "credential" in write(engine, path, SECRET[20:], mode="append")
    assert path.read_text(encoding="utf-8") == SECRET[:20]


def test_edit_checks_final_text_not_only_replacement(workspace):
    engine, repo = workspace
    path = repo / "app.txt"
    original = SECRET[:-8] + "PLACEHOLDER"
    path.write_text(original, encoding="utf-8")
    result = engine.exec_tool("edit_file", json.dumps({"filename": str(path),
        "old_string": "PLACEHOLDER", "new_string": SECRET[-8:]}))
    assert "credential" in result and SECRET not in result
    assert path.read_text(encoding="utf-8") == original and engine._turn_writes == 0


def test_ordinary_text_and_short_values_pass(workspace):
    engine, repo = workspace
    assert write(engine, repo / "app.txt", "ordinary short text").startswith("Wrote ")


def test_off_switch_allows_complete_and_boundary_secret(workspace, monkeypatch):
    engine, repo = workspace
    monkeypatch.setattr(engine, "BLOCK_SECRETS_IN_WRITES", False)
    path = repo / "app.txt"
    path.touch()
    assert write(engine, path, SECRET[:-8]).startswith("Wrote ")
    assert write(engine, path, SECRET[-8:], mode="append").startswith("Appended ")
    assert path.read_text(encoding="utf-8") == SECRET


def test_denial_audit_contains_path_not_secret(workspace, monkeypatch):
    engine, repo = workspace
    records = []
    monkeypatch.setattr(engine, "_audit", lambda *args, **kwargs: records.append(kwargs))
    write(engine, repo / "app.txt", SECRET)
    assert any(item.get("reason") == "secret_in_file" for item in records)
    assert SECRET not in json.dumps(records)


def test_downloaded_content_is_scanned_before_writing(workspace, monkeypatch):
    engine, repo = workspace
    monkeypatch.setattr(engine, "_fetch_url_bytes", lambda url: (SECRET.encode(), None, "text/plain"))
    path = repo / "app.txt"
    result = engine.exec_tool("write_file", json.dumps({"filename": str(path),
        "source_url": "https://example.com/app.txt"}))
    assert "credential" in result and not path.exists()


def test_unicode_secret_split_across_append_boundary(workspace, monkeypatch):
    engine, repo = workspace
    secret = "credential-é-🔒-123456789"
    monkeypatch.setattr(engine, "_SECRET_VALUES", {secret})
    path = repo / "app.txt"
    path.write_text("x" * 100000 + secret[:-5], encoding="utf-8")
    assert "credential" in write(engine, path, secret[-5:], mode="append")
    assert not path.read_text(encoding="utf-8").endswith(secret)
