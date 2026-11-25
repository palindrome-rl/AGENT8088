"""Two wrong "not verified" verdicts from runs on staging, and what was behind them.

1. A build fix. The model fixed a typo in a source file, ran the project's `python build.py`, which succeeded and wrote
   out/, read the output and gave a full answer. The build's own output counted as new unchecked work, so a completion
   check sent the answer back twice asking for evidence the build had just given; the model then gave an empty reply,
   the run ended with the engine's "I wasn't able to produce an answer to that.", and that sentence was read as the
   model saying part of the request was not done.
2. A price comparison. "compare with four competitor websites ... write a short brief" named websites to read, and the
   run that delivered the spreadsheet, brief and script was marked "no runnable app was produced for a request to
   build a web app".
"""
import json

import pytest

from tests.test_completion_pipeline import Script, _call, eng  # noqa: F401


def _tool(engine_module_name="agent8088.engine", build_writes=True, build_output="build ok: 120 modules, 121 symbols"):
    """write_file changes a file; `python build.py` succeeds and writes its output, as the real build does."""
    import importlib

    def tool(name, arguments, depth=0):
        engine_module = importlib.import_module(engine_module_name)
        args = json.loads(arguments)
        if name == "write_file":
            engine_module.note_mutation(name)
            path = engine_module.resolve_write_path(args["filename"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(args.get("content", ""), encoding="utf-8")
            return f"Wrote {path}"
        if name == "execute_shell" and "build.py" in args.get("command", "") and build_writes:
            engine_module.note_mutation(name)
            if "build ok" in build_output:
                out = engine_module.PROJECT_ROOT / "out"
                out.mkdir(exist_ok=True)
                (out / "manifest.json").write_text('{"modules": 120}', encoding="utf-8")
            return build_output
        return "ok"
    return tool


def _run(engine, request):
    trace = []
    answer = engine.run_agent([{"role": "user", "content": request}], max_turns=12, tools_def=[],
                              allowed_tools={"write_file", "execute_shell", "read_text"}, trace=trace)
    return answer, trace


REQUEST = "The build fails. Fix it so `python build.py` succeeds and produces out/manifest.json."


def test_a_clean_build_after_the_fix_is_the_evidence(eng, monkeypatch):
    (eng.PROJECT_ROOT / "build.py").write_text("print('build')\n", encoding="utf-8")
    script = Script(eng, monkeypatch, [
        _call("write_file", filename="src/module_077.txt", content="use parse_header\n"),
        _call("execute_shell", command="python build.py"),
        "Fixed the typo in module_077.txt; the build now succeeds.",
    ], tool=_tool())
    answer, trace = _run(eng, REQUEST)
    assert not script.nudges("no fresh verification evidence")
    assert answer.startswith("Fixed the typo in module_077.txt")
    assert "no fresh automated verification evidence" not in answer


def test_a_build_that_fails_is_no_evidence(eng, monkeypatch):
    (eng.PROJECT_ROOT / "build.py").write_text("print('build')\n", encoding="utf-8")
    script = Script(eng, monkeypatch, [
        _call("write_file", filename="src/module_077.txt", content="use parse_heder\n"),
        _call("execute_shell", command="python build.py"),
        "Fixed it.",
    ], tool=_tool(build_output="link error: module_077 uses parse_heder\nCommand exited with status 1."))
    answer, _trace = _run(eng, REQUEST)
    assert "Fixed it." in answer and not answer.startswith("Fixed it.")   # a headline or a note says it was not settled


@pytest.mark.parametrize("command, output, counts", [
    ("python build.py", "build ok", True),
    ("npm run build", "built in 3.2s", True),
    ("make", "done", True),
    ("python build.py", "Traceback (most recent call last):\n  File x", False),
    ("python build.py", "2 tests failed", False),
    ("python build.py", "Error: no such file", False),
    ("echo built", "built", False),
    ("python -c \"print(1)\"", "1", False),
])
def test_which_clean_runs_count(eng, command, output, counts):
    from agent8088.trajectory import TrajectoryState
    state = TrajectoryState({}, goal="fix the build", message_count=1)
    state.data["verification"] = "pending"
    assert eng._runs_project_program("execute_shell", {"command": command}, output, state) is counts


def test_a_script_this_run_wrote_is_not_the_projects_program(eng):
    from agent8088.trajectory import TrajectoryState
    state = TrajectoryState({}, goal="write a report script", message_count=1)
    state.data["verification"] = "pending"
    state.data["written_paths"] = [str(eng.PROJECT_ROOT / "report.py")]
    assert eng._runs_project_program("execute_shell", {"command": "python report.py"}, "ok", state) is False


def test_no_final_answer_gives_the_answer_that_was_sent_back(eng, monkeypatch):
    script = Script(eng, monkeypatch, [
        _call("write_file", filename="notes.txt", content="x"),
        "I wrote notes.txt with the summary you asked for.",
        "",
        "",
        "",
    ], tool=_tool())
    answer, trace = _run(eng, "write notes.txt with a summary")
    assert script.calls >= 3, "the reply must have been sent back for this test to mean anything"
    assert "I wrote notes.txt with the summary you asked for." in answer
    assert eng.NO_FINAL_REPLY_NOTE in answer
    assert "the reply says part of the request was not done" not in answer
    assert eng.NO_ANSWER_TEXT not in answer


def test_the_engines_own_sentence_is_never_read_as_the_model_saying_it_did_not_finish(eng):
    from agent8088.trajectory import TrajectoryState
    state = TrajectoryState({}, goal="fix it", message_count=1)
    state.data["mutation_count"] = 2
    reasons = eng._not_verified_reasons(state, eng.NO_ANSWER_TEXT)
    assert "the reply says part of the request was not done" not in reasons
    assert "the model gave no final answer" in reasons
    held = "I fixed it.\n\n" + eng.NO_FINAL_REPLY_NOTE
    assert "the model gave no final answer" not in eng._not_verified_reasons(state, held)


def test_the_models_own_admission_still_counts(eng):
    from agent8088.trajectory import TrajectoryState
    state = TrajectoryState({}, goal="fix it", message_count=1)
    reasons = eng._not_verified_reasons(state, "I could not create the folder where it was asked for.")
    assert "the reply says part of the request was not done" in reasons


@pytest.mark.parametrize("request_text, wants", [
    ("Build a small web app that tracks expenses", True),
    ("Create an HTML page showing the chart", True),
    ("Please write a browser game where tiles flip", True),
    ("Develop the front-end for our todo app", True),
    ("Build me a simple website", True),
    ("build a tile flipping web-based game", True),
    ("Create a web page that lists my books", True),
    ("I want to know how we compare with four competitor websites that are running on this computer. "
     "Write a short brief from it.", False),
    ("Write a scraper for the product pages on these web pages", False),
    ("Build a script that checks prices on competitor websites", False),
    ("Fix the bug in the build", False),
])
def test_a_web_app_is_what_the_build_verb_makes(request_text, wants):
    from agent8088.trajectory import TrajectoryState
    assert TrajectoryState({}, goal=request_text, message_count=1).wants_web_app() is wants


def test_a_price_comparison_that_reads_websites_needs_no_app(eng):
    from agent8088.trajectory import TrajectoryState
    goal = ("Please do a competitor price check for us and write a short marketing brief from it. I want to know how "
            "we compare with four competitor websites that are running on this computer.")
    state = TrajectoryState({}, goal=goal, message_count=1)
    state.data["mutation_count"] = 3
    state.data["written_paths"] = ["outputs/price_comparison.xlsx", "outputs/marketing_brief.md", "outputs/watch.py"]
    assert not state.no_runnable_app()
