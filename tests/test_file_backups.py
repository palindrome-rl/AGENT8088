"""Files the agent overwrites are copied aside first, so a bad overwrite is recoverable."""
import json
import os
import time

import pytest


@pytest.fixture
def bk(engine, tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    data = tmp_path / "data"
    monkeypatch.setattr(engine, "_agent_data_dir", lambda: data)
    monkeypatch.setattr(engine, "FILE_BACKUPS", True)
    monkeypatch.setattr(engine, "DISPOSABLE_CONTAINER", False)
    engine.ALLOWED_PATHS = [work]
    engine.PERMISSION_MODE = "full-auto"
    engine.PLAN_AUDIT = False
    engine.reset_turn_counters()
    engine.work, engine.data = work, data
    return engine


def write(engine, path, content, **extra):
    return engine.exec_tool("write_file", json.dumps({"filename": str(path), "content": content, **extra}))


def backups(engine):
    root = engine.data / "backups"
    return sorted(p for p in root.rglob("*") if p.is_file() and p.name != "index.jsonl") if root.exists() else []


def test_overwriting_an_existing_file_keeps_its_original_bytes(bk):
    target = bk.work / "app.py"
    original = b"line one\r\nline two \xff\xfe binary-ish\r\n"
    target.write_bytes(original)
    assert write(bk, target, "print('new')").startswith("Wrote ")
    assert target.read_text(encoding="utf-8") == "print('new')"
    saved = backups(bk)
    assert len(saved) == 1 and saved[0].read_bytes() == original and saved[0].name.endswith("-app.py")


def test_a_new_file_gets_no_backup(bk):
    write(bk, bk.work / "fresh.txt", "hello")
    assert backups(bk) == []
    assert not (bk.data / "backups").exists()


def test_only_the_first_change_in_a_session_is_backed_up(bk):
    target = bk.work / "app.py"
    target.write_text("ORIGINAL", encoding="utf-8")
    write(bk, target, "second")
    write(bk, target, "third")
    saved = backups(bk)
    assert len(saved) == 1 and saved[0].read_text(encoding="utf-8") == "ORIGINAL"


def test_a_file_the_agent_created_is_backed_up_before_its_first_rewrite(bk):
    target = bk.work / "made.py"
    write(bk, target, "version one")
    write(bk, target, "version two")
    assert [p.read_text(encoding="utf-8") for p in backups(bk)] == ["version one"]


def test_edit_file_backs_up_the_file(bk):
    target = bk.work / "app.py"
    target.write_text("alpha beta gamma", encoding="utf-8")
    result = bk.exec_tool("edit_file", json.dumps({"filename": str(target), "old_string": "beta", "new_string": "BETA"}))
    assert "Updated" in result and target.read_text(encoding="utf-8") == "alpha BETA gamma"
    assert [p.read_text(encoding="utf-8") for p in backups(bk)] == ["alpha beta gamma"]


def test_an_append_series_is_backed_up_once_at_its_first_part(bk):
    target = bk.work / "long.txt"
    target.write_text("ORIGINAL\n", encoding="utf-8")
    for part in ("one\n", "two\n", "three\n"):
        write(bk, target, part, mode="append")
    assert target.read_text(encoding="utf-8") == "ORIGINAL\none\ntwo\nthree\n"
    saved = backups(bk)
    assert len(saved) == 1 and saved[0].read_text(encoding="utf-8") == "ORIGINAL\n"


def test_a_downloaded_file_overwriting_an_existing_one_is_backed_up(bk, monkeypatch):
    target = bk.work / "image.png"
    target.write_bytes(b"OLD IMAGE")
    monkeypatch.setattr(bk, "_fetch_url_bytes", lambda url: (b"NEW IMAGE", "", "image/png"))
    bk.exec_tool("write_file", json.dumps({"filename": str(target), "source_url": "https://example.com/x.png"}))
    assert target.read_bytes() == b"NEW IMAGE"
    assert [p.read_bytes() for p in backups(bk)] == [b"OLD IMAGE"]


def test_the_tool_result_is_identical_with_backups_on_or_off(bk, monkeypatch):
    a, b = bk.work / "a.txt", bk.work / "b.txt"
    a.write_text("x", encoding="utf-8")
    b.write_text("x", encoding="utf-8")
    on = write(bk, a, "new").replace(str(a), "FILE")
    monkeypatch.setattr(bk, "FILE_BACKUPS", False)
    off = write(bk, b, "new").replace(str(b), "FILE")
    assert on == off


def test_the_off_switch(bk, monkeypatch):
    monkeypatch.setattr(bk, "FILE_BACKUPS", False)
    target = bk.work / "app.py"
    target.write_text("ORIGINAL", encoding="utf-8")
    write(bk, target, "new")
    assert backups(bk) == []


def test_no_backups_in_a_disposable_container(bk, monkeypatch):
    monkeypatch.setattr(bk, "DISPOSABLE_CONTAINER", True)
    target = bk.work / "app.py"
    target.write_text("ORIGINAL", encoding="utf-8")
    write(bk, target, "new")
    assert backups(bk) == []


def test_two_files_with_the_same_name_get_separate_backups(bk):
    first, second = bk.work / "a" / "app.py", bk.work / "b" / "app.py"
    for path, text in ((first, "FIRST"), (second, "SECOND")):
        path.parent.mkdir()
        path.write_text(text, encoding="utf-8")
        write(bk, path, "new")
    assert sorted(p.read_text(encoding="utf-8") for p in backups(bk)) == ["FIRST", "SECOND"]


def test_the_index_lists_every_backup(bk):
    target = bk.work / "app.py"
    target.write_text("ORIGINAL", encoding="utf-8")
    write(bk, target, "new")
    index = next((bk.data / "backups").rglob("index.jsonl"))
    entry = json.loads(index.read_text(encoding="utf-8").splitlines()[0])
    assert entry["path"] == str(target.resolve()) and entry["bytes"] == 8
    assert os.path.isfile(entry["backup"])


# --- limits and failures ------------------------------------------------------------------------

def test_a_file_over_the_per_file_cap_is_skipped_but_still_written(bk, monkeypatch):
    monkeypatch.setattr(bk, "FILE_BACKUP_MAX_FILE_BYTES", 10)
    target = bk.work / "big.txt"
    target.write_text("x" * 100, encoding="utf-8")
    assert write(bk, target, "small").startswith("Wrote ")
    assert backups(bk) == [] and target.read_text(encoding="utf-8") == "small"


def test_the_session_total_is_capped(bk, monkeypatch):
    monkeypatch.setattr(bk, "FILE_BACKUP_MAX_SESSION_BYTES", 25)
    for name in ("one", "two", "three"):
        path = bk.work / f"{name}.txt"
        path.write_text("y" * 10, encoding="utf-8")
        write(bk, path, "new")
    assert len(backups(bk)) == 2


def test_a_failing_backup_never_blocks_the_write(bk, monkeypatch):
    target = bk.work / "app.py"
    target.write_text("ORIGINAL", encoding="utf-8")

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(bk.shutil, "copy2", boom)
    assert write(bk, target, "new").startswith("Wrote ")
    assert target.read_text(encoding="utf-8") == "new"


def test_old_backup_sessions_are_deleted_current_ones_kept(bk, monkeypatch):
    root = bk.data / "backups"
    old, recent = root / "20200101-000000-1", root / "20261001-000000-2"
    for folder in (old, recent):
        folder.mkdir(parents=True)
        (folder / "f").write_text("x", encoding="utf-8")
    stale = time.time() - 40 * 86400
    os.utime(old, (stale, stale))
    monkeypatch.setattr(bk, "FILE_BACKUP_KEEP_DAYS", 14)
    target = bk.work / "app.py"
    target.write_text("ORIGINAL", encoding="utf-8")
    write(bk, target, "new")
    assert not old.exists() and recent.exists()
    assert any(p.parent.name == bk._BACKUP_SESSION for p in backups(bk))


def test_keep_days_zero_keeps_everything(bk, monkeypatch):
    old = bk.data / "backups" / "20200101-000000-1"
    old.mkdir(parents=True)
    stale = time.time() - 400 * 86400
    os.utime(old, (stale, stale))
    monkeypatch.setattr(bk, "FILE_BACKUP_KEEP_DAYS", 0)
    target = bk.work / "app.py"
    target.write_text("ORIGINAL", encoding="utf-8")
    write(bk, target, "new")
    assert old.exists()


def test_a_refused_write_still_leaves_the_original_in_place(bk):
    """The backup is a copy, never a move: a blocked or failed write leaves the file."""
    target = bk.work / "app.py"
    target.write_text("ORIGINAL", encoding="utf-8")
    result = bk.exec_tool("edit_file", json.dumps({"filename": str(target), "old_string": "NOT PRESENT", "new_string": "x"}))
    assert "Updated" not in result
    assert target.read_text(encoding="utf-8") == "ORIGINAL"
