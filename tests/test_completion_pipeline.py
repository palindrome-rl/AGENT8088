"""The completion pipeline in the agent loop, driven by a scripted model."""
import json
from pathlib import Path
from types import SimpleNamespace as N

import pytest


def _reply(content):
    return N(choices=[N(message=N(content=content, tool_calls=None), finish_reason="stop")])


def _call(name, **args):
    return f"✿FUNCTION✿: {name} ✿ARGS✿: {json.dumps(args)}"


class Script:
    """Replies in order (the last repeats); records what the model was sent last."""

    def __init__(self, engine, monkeypatch, replies, tool=None):
        self.replies = list(replies)
        self.sent = []          # the final user/harness message before each model call
        self.calls = 0

        def completion(messages, *_a, **_k):
            self.sent.append(str(messages[-1].get("content") or ""))
            reply = self.replies[min(self.calls, len(self.replies) - 1)]
            self.calls += 1
            return _reply(reply)

        monkeypatch.setattr(engine, "_create_completion_with_fallback", completion)
        if tool is not None:
            monkeypatch.setattr(engine, "exec_tool", tool)

    def nudges(self, marker):
        return [m for m in self.sent if marker in m]


def run(engine, request, script, tools=("write_file", "execute_shell"), max_turns=12):
    return engine.run_agent([{"role": "user", "content": request}], max_turns=max_turns,
                            tools_def=[], allowed_tools=set(tools))


@pytest.fixture
def eng(engine, tmp_path):
    engine.ALLOWED_PATHS = [tmp_path]
    engine.PERMISSION_MODE = "full-auto"
    engine.PROJECT_ROOT = tmp_path
    engine.ARTIFACTS_ROOT = tmp_path / "artifacts"
    return engine


def failing_then_ok(results):
    """Results in order, then "ok". A write counts as a change, as the real tool would."""
    results = list(results)

    def tool(name, arguments, depth=0):
        if name == "write_file":
            import agent8088.engine as engine_module
            engine_module.note_mutation(name)
            args = json.loads(arguments)
            # Match the real write contract: explicitly requested filenames
            # belong at the project root; unspecified files use artifacts/.
            path = engine_module.resolve_write_path(args["filename"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(args.get("content", ""), encoding="utf-8")
        return results.pop(0) if results else "ok"
    return tool


# -- 1. unfinished work ----------------------------------------------------------------

def test_a_reply_that_says_not_done_continues_instead_of_ending(eng, monkeypatch):
    script = Script(eng, monkeypatch, [
        _call("execute_shell", command="build.sh"),      # fails
        "Not finished yet - the build still fails and I still need to fix it.",
        _call("execute_shell", command="build.sh"),      # succeeds
        "The build now passes.",
    ], tool=failing_then_ok(["Error: build failed", "built ok"]))
    answer = run(eng, "make the build work", script)
    assert script.nudges("Your reply says the work is not finished")
    assert "final answer" not in script.nudges("Your reply says the work is not finished")[0].lower()
    assert answer.startswith("The build now passes.")


def test_normal_phrasing_after_a_finished_task_is_not_second_guessed(eng, monkeypatch):
    script = Script(eng, monkeypatch, [
        _call("execute_shell", command="echo hi"),
        "Done. Next steps you could take: add CI. Still need to tell you: the file is ready.",
    ], tool=failing_then_ok(["hi"]))
    run(eng, "say hi", script)
    assert not script.nudges("not finished")
    assert script.calls == 2


def test_promising_work_without_doing_any_is_caught(eng, monkeypatch):
    script = Script(eng, monkeypatch, [
        "I have not yet written the app, I'll proceed now.",
        _call("execute_shell", command="make app"),
        "The app is built.",
    ], tool=failing_then_ok(["built"]))
    answer = run(eng, "build me an app", script)
    assert script.nudges("not finished")
    assert answer.startswith("The app is built.")


def test_an_impossible_task_ends_after_the_budget_with_an_honest_note(eng, monkeypatch):
    script = Script(eng, monkeypatch, [
        _call("execute_shell", command="fix it"),
        "Not finished yet, I still need to get this working.",
    ], tool=failing_then_ok(["Error: still broken"]))
    answer = run(eng, "fix the server", script)
    assert len(script.nudges("not finished")) == eng.MAX_COMPLETION_CONTINUATIONS == 3
    assert "Not completed" in answer and "unfinished" in answer


# -- 2. named files ----------------------------------------------------------------------

def test_a_named_file_that_does_not_exist_is_asked_for(eng, monkeypatch, tmp_path):
    target = tmp_path / "result.txt"

    def tool(name, arguments, depth=0):
        args = json.loads(arguments)
        target.write_text(args["content"], encoding="utf-8")
        return f"Wrote {len(args['content'])} bytes to {target}"

    script = Script(eng, monkeypatch, [
        "All done, result.txt has the answer.",
        _call("write_file", filename=str(target), content="42"),
        "I created the file.",
    ], tool=tool)
    run(eng, f"Create {target.as_posix()} containing the answer", script)
    assert script.nudges("missing or empty")
    assert target.read_text(encoding="utf-8") == "42"


def test_no_named_files_means_no_check(eng, monkeypatch):
    script = Script(eng, monkeypatch, ["The capital of France is Paris."])
    run(eng, "What is the capital of France?", script)
    assert script.calls == 1


# -- 4. multi-part requests ---------------------------------------------------------------

def test_a_forgotten_part_of_a_two_part_request_is_attempted(eng, monkeypatch):
    script = Script(eng, monkeypatch, [
        "Sales figures summary: revenue grew 12 percent.",
        "The biggest risks are churn, pricing and supply.",
    ])
    answer = run(eng, "Please:\n1. summarize the sales figures\n2. list the three biggest risks", script)
    nudge = script.nudges("not been addressed")
    assert len(nudge) == 1 and "three biggest risks" in nudge[0]
    assert "final answer" not in nudge[0].lower()
    assert answer.startswith("The biggest risks")


def test_a_reply_covering_every_part_ends_the_run(eng, monkeypatch):
    script = Script(eng, monkeypatch, [
        "Sales figures summary: grew 12 percent. The three biggest risks: churn, pricing, supply."])
    run(eng, "1. summarize the sales figures\n2. list the three biggest risks", script)
    assert script.calls == 1


def test_a_single_paragraph_request_builds_no_checklist(eng, monkeypatch):
    script = Script(eng, monkeypatch, ["Here is a summary."])
    run(eng, "Summarize the quarterly report and keep it brief", script)
    assert script.calls == 1


# -- 0. empty replies -----------------------------------------------------------------------

def test_an_empty_reply_mid_task_is_told_to_continue(eng, monkeypatch):
    script = Script(eng, monkeypatch, [
        _call("execute_shell", command="make"),   # fails -> work is pending
        "",
        _call("execute_shell", command="make"),
        "Built.",
    ], tool=failing_then_ok(["Error: nope", "built"]))
    answer = run(eng, "make it build", script)
    empty = script.nudges("Your last reply was empty")
    assert len(empty) == 1 and "Continue the task" in empty[0]
    assert "final answer" not in empty[0].lower()
    assert answer.startswith("Built.")


def test_an_empty_reply_with_no_pending_work_keeps_the_one_retry_wording(eng, monkeypatch):
    script = Script(eng, monkeypatch, ["", "Hello!"])
    answer = run(eng, "hi", script)
    assert len(script.nudges("You did not provide an answer")) == 1
    assert answer == "Hello!"


def test_repeated_empty_replies_end_with_the_fixed_message(eng, monkeypatch):
    script = Script(eng, monkeypatch, [
        _call("execute_shell", command="make"),
        "",
    ], tool=failing_then_ok(["Error: nope"]))
    answer = run(eng, "make it build", script)
    assert len(script.nudges("Your last reply was empty")) == 3
    assert "wasn't able to produce an answer" in answer


# -- 5. verification and tests -----------------------------------------------------------------

def test_the_verification_reminder_says_continue_and_run_it(eng, monkeypatch):
    # The harness's own smoke run of app.py would already count as a check (a passing page or script check ends the
    # "pending" state); it is off here so the wording of the reminder is the subject.
    monkeypatch.setattr(eng, "COMPLETION_SMOKE_TEST", "off")
    script = Script(eng, monkeypatch, [
        _call("write_file", filename="app.py", content="print(1)"),
        "I wrote app.py.",
        _call("execute_shell", command="python app.py"),
        "It prints 1.",
    ], tool=failing_then_ok(["Wrote 8 bytes to app.py", "1"]))
    run(eng, "write app.py that prints 1", script)
    nudge = script.nudges("no fresh verification evidence")
    assert nudge and "run what you changed" in nudge[0]
    assert "final answer" not in nudge[0].lower()


def test_a_scratch_script_gets_no_tests_reminder_even_in_a_project_with_tests(eng, monkeypatch, tmp_path):
    (tmp_path / "tests").mkdir()
    script = Script(eng, monkeypatch, [
        _call("write_file", filename=str(tmp_path / "scrape_prices.py"), content="print(1)"),
        _call("execute_shell", command="python scrape_prices.py"),
        "Scraped.",
    ], tool=failing_then_ok(["Wrote 8 bytes", "1"]))
    run(eng, "scrape the prices", script)
    assert not script.nudges("library files changed without tests")


def test_library_code_in_a_project_with_tests_gets_the_reminder(eng, monkeypatch, tmp_path):
    monkeypatch.setattr(eng, "COMPLETION_SMOKE_TEST", "off")   # the file is not run here; the reminder is the subject
    (tmp_path / "tests").mkdir()
    script = Script(eng, monkeypatch, [
        _call("write_file", filename=str(tmp_path / "src" / "parser.py"), content="x = 1"),
        _call("execute_shell", command="python -c pass"),
        "Added the parser.",
        "Tests not applicable here.",
    ], tool=failing_then_ok(["Wrote 5 bytes", "ok"]))
    run(eng, "add a parser module", script)
    nudge = script.nudges("library files changed without tests")
    assert nudge and "generate_tests" in nudge[0] and "final answer" not in nudge[0].lower()


def test_no_tests_reminder_in_a_project_without_a_test_suite(eng, monkeypatch, tmp_path):
    script = Script(eng, monkeypatch, [
        _call("write_file", filename=str(tmp_path / "src" / "parser.py"), content="x = 1"),
        _call("execute_shell", command="python -c pass"),
        "Added the parser.",
    ], tool=failing_then_ok(["Wrote 5 bytes", "ok"]))
    run(eng, "add a parser module", script)
    assert not script.nudges("without tests")


# -- the shared budget ------------------------------------------------------------------------------

def test_zero_turns_every_completion_check_off(eng, monkeypatch):
    monkeypatch.setattr(eng, "MAX_COMPLETION_CONTINUATIONS", 0)
    script = Script(eng, monkeypatch, [
        _call("execute_shell", command="x"),
        "Not finished yet, I still need to fix it.",
    ], tool=failing_then_ok(["Error: nope"]))
    answer = run(eng, "fix it", script)
    assert script.calls == 2 and answer.startswith("Not finished yet")


def test_progress_between_nudges_keeps_a_long_task_going(eng, monkeypatch):
    """Each continuation writes a file (real progress), so the budget of 3 is not spent."""
    monkeypatch.setattr(eng, "MAX_COMPLETION_CONTINUATIONS", 1)
    replies = []
    for n in range(3):
        replies += ["Not done yet - still need to write more.",
                    _call("write_file", filename=f"part{n}.txt", content="x")]
    replies.append("All parts written.")
    script = Script(eng, monkeypatch, replies, tool=failing_then_ok(["Error: setup failed"] * 0 + ["Wrote 1 bytes"] * 3))

    def tool(name, arguments, depth=0):
        eng.note_mutation(name)
        return "Wrote 1 bytes"

    monkeypatch.setattr(eng, "exec_tool", tool)
    answer = run(eng, "write the parts", script, max_turns=30)
    assert answer.startswith("All parts written.")
    assert len(script.nudges("not finished")) == 3


def test_replanning_is_separate_from_the_budget(eng, monkeypatch):
    script = Script(eng, monkeypatch, [
        _call("execute_shell", command="a"),
        _call("execute_shell", command="b"),
        "Gave up.",
    ], tool=failing_then_ok(["Error: one", "Error: two"]))
    run(eng, "do it", script)
    assert script.nudges("two recent actions failed")


def _supplied_example(tmp_path):
    (tmp_path / "artifacts").mkdir(exist_ok=True)
    (tmp_path / "artifacts" / "example.json").write_text("{}", encoding="utf-8")


def test_a_supplied_file_the_request_points_to_is_not_demanded_as_output(eng, monkeypatch, tmp_path):
    _supplied_example(tmp_path)
    script = Script(eng, monkeypatch, [
        _call("write_file", filename="index.html", content="<html></html>"),
        "The page is written.",
    ], tool=failing_then_ok([]))
    answer = run(eng, "Build a page that can export its data to a JSON file (the format is in "
                      "example.json). Put the page in index.html.", script)
    assert not script.nudges("missing or empty")
    assert "example.json" not in answer


def test_a_file_the_request_asks_to_be_created_is_still_demanded(eng, monkeypatch, tmp_path):
    _supplied_example(tmp_path)
    script = Script(eng, monkeypatch, [
        _call("write_file", filename="index.html", content="<html></html>"),
        "The page is written.",
    ], tool=failing_then_ok([]))
    run(eng, "Build a page and save the data to example.json. Put the page in index.html.", script)
    assert script.nudges("example.json")


def test_a_file_the_real_write_tool_diverted_is_not_demanded_again(eng, monkeypatch, tmp_path):
    """The write tool keeps a file it was not told to put there in artifacts/; the reply that names it
    must not be sent back to create it at the path that was asked for (which every write would divert)."""
    asked = tmp_path / "outputs" / "watch.py"
    script = Script(eng, monkeypatch, [
        _call("write_file", filename=str(asked), content="print('hi')\n"),
        "I wrote watch.py as asked.",
    ])
    answer = run(eng, "Write a script that prints hi.", script)
    assert (tmp_path / "artifacts" / "outputs" / "watch.py").is_file() and not asked.exists()
    assert not script.nudges("missing or empty"), script.sent
    assert answer.startswith("I wrote watch.py")


def test_a_diverted_file_still_gets_the_end_of_run_smoke_test(eng, monkeypatch, tmp_path):
    """The checks that open the written files must look where the file landed, not where it was asked for."""
    import shutil
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    monkeypatch.setattr(eng, "COMPLETION_SMOKE_TEST", "enforce")
    monkeypatch.setattr(eng, "_smoke_script_runner", lambda target: None)   # syntax check only
    asked = tmp_path / "outputs" / "broken.js"
    script = Script(eng, monkeypatch, [
        _call("write_file", filename=str(asked), content="function broken( {\n"),
        "I wrote the script.",
        _call("write_file", filename=str(asked), content="function fixed() { return 1; }\n"),
        "I fixed the script.",
    ])
    answer = run(eng, "Write a script that does something.", script)
    assert script.nudges("smoke test of what you built failed"), script.sent
    assert "syntax error" in " ".join(script.sent)
