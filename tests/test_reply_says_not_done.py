"""The verdict must not call work verified when the answer itself says a part was not done.

Seen in a benchmark run: the sandbox refused the virtual environment in the folder the request named, so the model
made it under artifacts/. Its answer said, under "Not done", that it could not create the folder where it was asked
for. The run's own checks found nothing wrong, and the footer said "Verification: exercised" with no NOT VERIFIED
headline. A grader found the folder missing.
"""
from agent8088 import engine
from agent8088.completion import reports_part_not_done
from agent8088.trajectory import TrajectoryState

PIP_ANSWER = (
    "Both files now exist at the requested paths with verified content.\n\n"
    "**Done:**\n- `report.py` and `totals.json` exist at the workspace root.\n\n"
    "**Not done / could not verify:**\n"
    "- I could **not** create a `.venv` at the workspace root. The sandbox refuses shell writes outside "
    "`artifacts/`, so the venv lives in `artifacts/.venv`.\n")


def test_a_not_done_section_is_a_part_not_done():
    assert reports_part_not_done(PIP_ANSWER)


def test_could_not_create_is_a_part_not_done():
    assert reports_part_not_done("Everything else works. I couldn't install the `tabulate` package into it.")
    assert reports_part_not_done("I was unable to create the output folder.")


def test_a_finished_answer_is_not():
    assert not reports_part_not_done("Done. I created `.venv`, installed tabulate and ran report.py; it printed "
                                     "four regions.")


def test_an_empty_not_done_list_is_not():
    assert not reports_part_not_done("**Done:** everything.\n\n**Not done:** nothing.")
    assert not reports_part_not_done("Not done: none.")


def test_a_limit_on_checking_is_not_a_part_not_done():
    # Not being able to check something is said in the verification level already; it is not a missing part.
    assert not reports_part_not_done("I could not verify it in a browser, and I could not test it on macOS.")
    assert not reports_part_not_done("I could not run the page in a browser here.")


def test_a_check_that_could_not_be_completed_is_not_a_part_not_done():
    # From a benchmark run whose page passed every grader check: the model meant its own click-through check.
    assert not reports_part_not_done(
        'One small caveat to flag honestly: the "no errors in the console" claim is based on the load-time render '
        "(clean), not on a full click-through, since that last part is the part I couldn't complete.")
    assert not reports_part_not_done("I could not finish the playthrough in a live browser.")


def test_work_that_could_not_be_completed_still_is():
    assert reports_part_not_done("I couldn't complete the build: the linker still fails on module_077.")
    assert reports_part_not_done("I could not finish the tests for the parser.")
    assert reports_part_not_done("I was unable to create the restart button.")


def test_a_blocked_step_worked_around_is_not():
    assert not reports_part_not_done("I could not write to out/ directly, so I built under artifacts/ and copied "
                                     "the files back.")
    assert not reports_part_not_done("I couldn't create the folder with mkdir; instead I used copy_file.")


def test_advice_to_the_user_is_not():
    assert not reports_part_not_done("If you could not install Node, use the installer from the website.")


def test_code_is_ignored():
    assert not reports_part_not_done("Done.\n```python\nraise SystemExit('could not create the cache')\n```\n")


def _changed_and_run(tmp_path):
    state = TrajectoryState({}, goal="Write report.py and run it.", message_count=1)
    state.data["mutation_count"] = 3
    state.data["ran_ok_at"] = 3
    return state


def test_the_headline_says_not_verified_when_the_answer_says_a_part_was_not_done(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "NOT_VERIFIED_HEADLINE", True)
    state = _changed_and_run(tmp_path)
    answer = engine._with_task_notes(PIP_ANSWER, state, read_only=True)
    assert answer.startswith("NOT VERIFIED:")
    assert "the reply says part of the request was not done" in answer.split("\n", 1)[0]


def test_a_finished_answer_keeps_its_level(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "NOT_VERIFIED_HEADLINE", True)
    state = _changed_and_run(tmp_path)
    answer = engine._with_task_notes("Done. report.py ran and printed the table.", state, read_only=True)
    assert not answer.startswith("NOT VERIFIED:")
    assert "Verification: exercised" in answer


def test_the_harness_note_for_open_parts_is_not_read_as_the_reply():
    # "Not completed: ..." is what the end-of-run notes write; an answer seen twice must not count it as the model's.
    assert not reports_part_not_done("Done.\n\nNot completed: add a summary command that prints the counts")
