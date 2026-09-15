import pytest

pytest.importorskip("tree_sitter_language_pack")


def test_repo_map_is_in_the_tool_catalogue(engine):
    assert engine.TOOL_SPECS["repo_map"].get("mode") == "repomap"


def test_repo_map_returns_an_outline_of_the_project(engine, tmp_path, monkeypatch):
    (tmp_path / "core.py").write_text("class Ledger:\n    def settle(self):\n        return 1\n", encoding="utf-8")
    (tmp_path / "user.py").write_text("from core import Ledger\n\nLedger().settle()\n", encoding="utf-8")
    monkeypatch.setattr(engine, "PROJECT_ROOT", tmp_path)
    result = engine.run_tool("repo_map", {})
    assert "core.py" in result
    assert "class Ledger" in result


def test_repo_map_honours_an_explicit_budget(engine, tmp_path, monkeypatch):
    for index in range(30):
        (tmp_path / f"mod{index}.py").write_text(
            "".join(f"def function_{index}_{n}():\n    return {n}\n\n" for n in range(15)),
            encoding="utf-8")
    monkeypatch.setattr(engine, "PROJECT_ROOT", tmp_path)
    result = engine.run_tool("repo_map", {"budget": "150"})
    assert len(result) // 4 <= 150


def test_repo_map_rejects_a_nonsense_budget(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "PROJECT_ROOT", tmp_path)
    assert "budget" in engine.run_tool("repo_map", {"budget": "banana"}).lower()


def test_repo_map_focus_accepts_a_comma_separated_list(engine, tmp_path, monkeypatch):
    (tmp_path / "a.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("def beta():\n    return 2\n", encoding="utf-8")
    (tmp_path / "user.py").write_text("alpha()\nbeta()\n", encoding="utf-8")
    monkeypatch.setattr(engine, "PROJECT_ROOT", tmp_path)
    result = engine.run_tool("repo_map", {"focus": "b.py, a.py"})
    assert "b.py" in result


def test_repo_map_reports_missing_tree_sitter_instead_of_raising(engine, tmp_path, monkeypatch):
    from agent8088 import repomap

    def explode(*_args, **_kwargs):
        raise repomap.RepoMapUnavailable("tree-sitter is not installed")

    monkeypatch.setattr(engine, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(repomap, "build_map", explode)
    result = engine.run_tool("repo_map", {})
    assert "tree-sitter" in result.lower()
