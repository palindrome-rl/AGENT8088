"""A file the request names, written by the run under artifacts/, is copied to the path the request named.

Seen in benchmark runs: told that relative paths land in artifacts/, the model wrote `artifacts/board.html` for a
request that asked for `board.html`. Four pages that worked when graded where they were (a game, a puzzle, a shop
page) were each left only in artifacts/ and graded as missing. The end-of-run copy-back placed diverted writes, but
not a file the model had written to artifacts/ itself.
"""
import json

from tests.test_completion_pipeline import Script, _call, eng  # noqa: F401

PAGE = "<!doctype html><title>Board</title><p>board</p>"


def _writer(eng):
    def tool(name, arguments, depth=0):
        args = json.loads(arguments)
        if name == "write_file":
            from pathlib import Path
            target = Path(args["filename"])
            target = target if target.is_absolute() else eng.ARTIFACTS_ROOT / target
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(args["content"], encoding="utf-8")
            eng.note_mutation(name)
            return f"Wrote {len(args['content'])} bytes to {target}"
        return "ok"
    return tool


def _run(eng, monkeypatch, filename, request="Please make a page as `board.html` that shows the board."):
    script = Script(eng, monkeypatch, [_call("write_file", filename=filename, content=PAGE), "Made board.html."],
                    tool=_writer(eng))
    answer = eng.run_agent([{"role": "user", "content": request}], max_turns=6, tools_def=[],
                           allowed_tools={"write_file"})
    return answer, script


def test_a_named_page_written_under_artifacts_is_copied_to_the_named_path(eng, monkeypatch):
    answer, _ = _run(eng, monkeypatch, str(eng.ARTIFACTS_ROOT / "board.html"))
    placed = eng.PROJECT_ROOT / "board.html"
    assert placed.read_text(encoding="utf-8") == PAGE
    assert (eng.ARTIFACTS_ROOT / "board.html").is_file(), "the artifacts copy is kept"
    assert "Also copied to the path you named" in answer
    assert "found only in" not in answer


def test_a_file_the_request_does_not_name_stays_in_artifacts(eng, monkeypatch):
    _run(eng, monkeypatch, str(eng.ARTIFACTS_ROOT / "helper.html"))
    assert not (eng.PROJECT_ROOT / "helper.html").exists()


def test_a_file_already_at_the_named_path_is_not_overwritten(eng, monkeypatch):
    mine = eng.PROJECT_ROOT / "board.html"
    mine.write_text("the person's own board", encoding="utf-8")
    _run(eng, monkeypatch, str(eng.ARTIFACTS_ROOT / "board.html"))
    assert mine.read_text(encoding="utf-8") == "the person's own board"


def test_a_named_file_in_a_subfolder_keeps_its_folder(eng, monkeypatch):
    _run(eng, monkeypatch, str(eng.ARTIFACTS_ROOT / "site" / "board.html"),
         request="Please make `site/board.html`, a page that shows the board.")
    assert (eng.PROJECT_ROOT / "site" / "board.html").read_text(encoding="utf-8") == PAGE
    assert not (eng.PROJECT_ROOT / "board.html").exists()


def test_the_missing_file_finding_is_dropped_once_the_file_is_placed(engine, tmp_path, monkeypatch):
    from agent8088.trajectory import TrajectoryState
    monkeypatch.setattr(engine, "PROJECT_ROOT", tmp_path)
    (tmp_path / "board.html").write_text(PAGE, encoding="utf-8")
    state = TrajectoryState({}, goal="make board.html", message_count=1)
    state.data["unmet_checks"] = ["missing or empty files: board.html (found only in X; create it at the requested path)",
                                  "missing or empty files: notes.md",
                                  "the latest check failed"]
    engine._drop_resolved_missing(state)
    assert state.data["unmet_checks"] == ["missing or empty files: notes.md", "the latest check failed"]


def test_a_budget_stop_during_a_model_call_still_places_the_named_file(eng, monkeypatch):
    # Seen in a benchmark run: the clock ran out while the model was answering, the stop was raised out of the loop,
    # and the copy-back ran after the run had already forgotten the person's message, so nothing counted as named and
    # the working page stayed in artifacts/.
    from tests.test_completion_pipeline import _reply
    calls = {"n": 0}

    def completion(messages, *_a, **_k):
        calls["n"] += 1
        if calls["n"] == 1:
            return _reply(_call("write_file", filename=str(eng.ARTIFACTS_ROOT / "board.html"), content=PAGE))
        raise eng.TurnBudgetExceeded("Time budget exceeded: 901 seconds elapsed (limit 900s).")

    monkeypatch.setattr(eng, "_create_completion_with_fallback", completion)
    monkeypatch.setattr(eng, "exec_tool", _writer(eng))
    answer = eng.run_agent([{"role": "user", "content": "Please make a page as `board.html` that shows the board."}],
                           max_turns=6, tools_def=[], allowed_tools={"write_file"})
    assert "Time budget exceeded" in answer
    assert (eng.PROJECT_ROOT / "board.html").read_text(encoding="utf-8") == PAGE
