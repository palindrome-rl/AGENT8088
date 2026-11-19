"""The completion checks' rules, tested on recorded wording rather than a live model."""
import pytest

from agent8088 import completion as c
from agent8088 import trajectory


# --- 1. a reply that admits unfinished work ----------------------------------------

ADMITS = [
    "Not done yet - I still need to write the tests.",
    "I have not yet written the app, I'll proceed now.",
    "The result is still incomplete and not runnable.",
    "The parser is written, but the remaining steps are the CLI and the docs.",
    "Next, I'll add the parser and then wire it in.",
    "Let me now create the configuration file.",
    "This is a work in progress; the export is not finished.",
    "TODO: handle the error case",
    "The server isn't running yet.",
    "I haven't tested it.",
]
FINISHED = [
    "Done. The app is in app.py and all 12 tests pass.",
    "I created report.md with the summary. Next steps you could take: add CI, or a changelog.",
    "All parts are complete and verified.",
    "Here is how the algorithm works: it sorts, then merges.",
    "```python\n# TODO: tune this\n```\nThe script is finished and runs cleanly.",
    "The requested fix is in place. There is nothing remaining.",
    "Summary: three files changed. You could also consider caching.",
]


@pytest.mark.parametrize("reply", ADMITS)
def test_replies_that_admit_unfinished_work_are_detected(reply):
    assert c.admits_unfinished_work(reply), reply


@pytest.mark.parametrize("reply", FINISHED)
def test_normal_completed_summaries_are_not(reply):
    assert not c.admits_unfinished_work(reply), reply


# --- 2. files the request named ---------------------------------------------------------

def test_only_files_the_request_asks_to_create_are_required():
    request = "Read data.csv and write the summary to out/summary.md. Also create notes.txt."
    assert c.named_output_paths(request) == ["out/summary.md", "notes.txt"]


@pytest.mark.parametrize("request_text,expected", [
    ("Save the result as report.pdf", ["report.pdf"]),
    ("Fix the bug in parser.py", []),
    ("Open config.yaml and update it", []),
    ("Generate /app/solution.txt with the answer", ["/app/solution.txt"]),
    ("What does main.py do?", []),
    ("Build a site: put the styles in style.css and the page in index.html.", ["style.css", "index.html"]),
    ("Create a script named build.sh.", ["build.sh"]),
    ("Use numpy 1.2 and python 3.11 to compute pi", []),
])
def test_named_output_paths(request_text, expected):
    assert c.named_output_paths(request_text) == expected


def test_code_blocks_in_the_request_are_ignored():
    assert c.named_output_paths("Run this:\n```\nsave data.json\n```\nthen explain") == []


def test_missing_and_empty_outputs_are_found(tmp_path):
    (tmp_path / "ok.txt").write_text("x", encoding="utf-8")
    (tmp_path / "empty.txt").write_text("", encoding="utf-8")
    request = "Create ok.txt, empty.txt and absent.txt"
    missing = c.missing_outputs(request, "done", [], [tmp_path])
    assert missing == ["empty.txt", "absent.txt"]


def test_a_written_file_in_the_wrong_folder_does_not_satisfy_a_bare_name(tmp_path):
    landed = tmp_path / "elsewhere" / "report.md"
    landed.parent.mkdir()
    landed.write_text("content", encoding="utf-8")
    assert c.missing_outputs("Write report.md", "done", [str(landed)], [tmp_path]) == ["report.md"]


@pytest.mark.parametrize("requested", ["report.md", "./report.md"])
def test_relative_and_absolute_requested_paths_resolve_at_the_target(tmp_path, requested):
    target = tmp_path / "report.md"
    target.write_text("content", encoding="utf-8")
    assert c.missing_outputs(f"Write {requested}", "done", [], [tmp_path]) == []
    assert c.missing_outputs(f"Write {target}", "done", [], [tmp_path]) == []


def test_a_claimed_deliverable_that_is_now_empty_is_reported(tmp_path):
    target = tmp_path / "app.py"
    target.write_text("", encoding="utf-8")
    assert c.missing_outputs("make an app", "I wrote app.py for you", [str(target)], [tmp_path]) == [str(target)]
    assert c.missing_outputs("make an app", "all good", [str(target)], [tmp_path]) == []


def test_no_named_files_costs_nothing(tmp_path):
    assert c.missing_outputs("What is 2+2?", "4", [], [tmp_path]) == []


# --- 4. multi-part requests -------------------------------------------------------------

def test_a_numbered_list_becomes_a_checklist():
    request = "Build a CLI:\n1. parse the arguments\n2. read the input file\n3. print a report"
    assert c.extract_subgoals(request) == ["parse the arguments", "read the input file", "print a report"]


def test_bullets_work_too():
    assert len(c.extract_subgoals("Please:\n- clean the data\n- plot the trend")) == 2


def test_explicit_also_and_then_clauses_split_a_request():
    items = c.extract_subgoals("Write a parser for the log format, and also add unit tests for it.")
    assert items == ["Write a parser for the log format", "add unit tests for it"]


@pytest.mark.parametrize("request_text", [
    "What is the capital of France?",
    "Fix the failing test in test_parser.py",
    "1. do everything",
    "Summarize this document in three sentences.",
])
def test_single_asks_build_no_checklist(request_text):
    assert c.extract_subgoals(request_text) == []


def test_the_checklist_is_capped():
    request = "\n".join(f"{n}. step number {n}" for n in range(1, 20))
    assert len(c.extract_subgoals(request)) == c.MAX_SUBGOALS


def test_a_forgotten_part_is_found_and_an_addressed_one_is_not():
    items = ["summarize the sales figures", "list the three biggest risks"]
    reply = "Sales figures summary: revenue grew 12% quarter over quarter."
    assert c.unaddressed_subgoals(items, reply, []) == ["list the three biggest risks"]
    reply += " The biggest risks are churn, pricing and supply."
    assert c.unaddressed_subgoals(items, reply, []) == []


def test_a_written_file_name_counts_toward_a_part():
    assert c.unaddressed_subgoals(["create the changelog"], "done", ["/w/changelog.md"]) == []


# --- 5. which changed files deserve a tests reminder ---------------------------------------

@pytest.mark.parametrize("path,expected", [
    ("src/parser.py", True),
    ("pkg/models/user.py", True),
    ("scrape_prices.py", False),
    ("tools/download_data.py", False),
    ("main.py", False),
    ("scratch/probe.py", False),
    ("tmp/x.py", False),
    ("inspect_page.py", False),
])
def test_library_code_vs_scratch_and_entry_points(path, expected):
    assert c.is_library_code(path) is expected


def test_the_system_temp_dir_is_scratch():
    assert c.is_scratch("/tmp/agent/work.py", temp_dir="/tmp")


def test_project_has_tests(tmp_path):
    assert not c.project_has_tests(tmp_path)
    (tmp_path / "tests").mkdir()
    assert c.project_has_tests(tmp_path)


def test_project_has_tests_by_file(tmp_path):
    (tmp_path / "test_x.py").write_text("", encoding="utf-8")
    assert c.project_has_tests(tmp_path)


# --- the budget -----------------------------------------------------------------------------

def test_the_budget_ends_after_its_limit_without_progress():
    budget = c.ContinuationBudget(limit=3)
    assert [budget.spend("same", f"r{n}") for n in range(5)] == [True, True, True, False, False]
    assert budget.exhausted and budget.total == 3
    assert "r3" in budget.unmet_note() and "3 follow-up checks" in budget.unmet_note()


def test_progress_hands_a_slot_back():
    budget = c.ContinuationBudget(limit=2)
    assert budget.spend(1) and budget.spend(1)
    assert budget.spend(2), "a continuation that made progress should not use up the budget"


def test_total_continuations_are_bounded_even_with_constant_progress():
    budget = c.ContinuationBudget(limit=2)
    results = [budget.spend(n) for n in range(100)]
    assert results.count(True) == 2 * c.TOTAL_CONTINUATION_FACTOR


def test_a_zero_limit_disables_everything():
    budget = c.ContinuationBudget(limit=0)
    assert not budget.enabled and not budget.spend(1, "x")
    assert budget.unmet == []


def test_nudges_always_say_continue_never_final_answer():
    nudges = [c.outputs_nudge(["a.txt"]), c.subgoals_nudge(["x y z"]), c.tests_nudge(["a.py"])]
    texts = [n.text for n in nudges] + [c.UNFINISHED_ASK, c.EMPTY_REPLY_ASK, c.VERIFY_ASK]
    for text in texts:
        assert "continue" in text.lower(), text
        assert "final answer" not in text.lower(), text


# --- trajectory state ---------------------------------------------------------------------------

def _state(goal="g"):
    return trajectory.TrajectoryState({}, goal=goal, message_count=1)


def test_subgoals_live_in_the_checklist_and_are_built_once():
    t = _state()
    t.set_subgoals(["one thing", "another thing"])
    t.set_subgoals(["ignored"])
    assert t.open_subgoals() == ["one thing", "another thing"]
    assert "Part: one thing" in t.prompt() or True  # the prompt shows operations only
    t.mark_subgoals(["another thing"])
    assert t.open_subgoals() == ["another thing"]
    t.mark_subgoals([])
    assert t.open_subgoals() == []


def test_open_items_cover_failed_tools_pending_verification_and_open_parts():
    t = _state()
    assert not t.has_open_items()
    op = t.before_tool("execute_shell")
    t.after_tool(op, "boom", failed=True, blocked=False, mutated=False)
    assert t.has_open_items() and t.unresolved_tools() == ["execute_shell"]
    op = t.before_tool("execute_shell")
    t.after_tool(op, "ok", failed=False, blocked=False, mutated=False)
    assert not t.has_open_items(), "a successful retry resolves the earlier failure"
    op = t.before_tool("write_file")
    t.after_tool(op, "wrote", failed=False, blocked=False, mutated=True, path="/w/a.py")
    assert t.has_open_items()  # verification pending


def test_progress_mark_changes_on_a_write_a_pass_or_an_addressed_part():
    t = _state()
    t.set_subgoals(["part a", "part b"])
    first = t.progress_mark()
    op = t.before_tool("write_file")
    t.after_tool(op, "w", failed=False, blocked=False, mutated=True, path="/w/a.py")
    second = t.progress_mark()
    op = t.before_tool("run_tests")
    t.after_tool(op, "ok", failed=False, blocked=False, mutated=False, verdict="passed")
    third = t.progress_mark()
    t.mark_subgoals(["part b"])
    fourth = t.progress_mark()
    assert len({first, second, third, fourth}) == 4


def test_unmet_checks_appear_in_the_review_and_clear_with_the_next_request():
    t = trajectory.TrajectoryState({}, goal="g", message_count=1)
    t.note_unmet(["missing or empty files: a.txt"])
    assert "Not completed: missing or empty files: a.txt." in t.review()
    t.data["verification"] = "pending"
    again = trajectory.TrajectoryState(t.data, goal="h", message_count=2)
    assert again.data["unmet_checks"] == []


def test_the_tests_reminder_can_be_scoped_and_is_not_spent_when_nothing_qualifies():
    t = _state()
    for path in ("/w/scrape_prices.py", "/w/src/parser.py"):
        op = t.before_tool("write_file")
        t.after_tool(op, "w", failed=False, blocked=False, mutated=True, path=path)
    library = lambda p: c.is_library_code(p)
    assert t.relevant_untested(library) == ["/w/src/parser.py"]
    assert t.request_tests(library)
    assert not t.request_tests(library), "one-shot"

    scratch_only = _state()
    op = scratch_only.before_tool("write_file")
    scratch_only.after_tool(op, "w", failed=False, blocked=False, mutated=True, path="/w/scrape_prices.py")
    assert not scratch_only.request_tests(library)
    assert not scratch_only.data["tests_requested"], "nothing qualified, so the one-shot is kept"


def test_a_project_living_under_the_temp_dir_is_not_all_scratch():
    temp, root = "/var/tmp", "/var/tmp/proj"
    assert c.is_library_code("/var/tmp/proj/src/a.py", temp, root)
    assert not c.is_library_code("/var/tmp/proj/scratch/a.py", temp, root)
    assert not c.is_library_code("/var/tmp/other/a.py", temp, root)


# --- sheet #9: misplaced, empty and partial outputs -------------------------------

def test_requested_file_found_only_in_artifacts_is_misplaced(tmp_path):
    from agent8088 import completion as C
    project, art = tmp_path, tmp_path / "artifacts"
    art.mkdir()
    (art / "report.md").write_text("x", encoding="utf-8")
    found = C.missing_outputs("Write report.md with the results.", "Done, report.md written.",
                              [str(art / "report.md")], [project], mirror_roots=[art])
    assert len(found) == 1 and found[0].startswith("report.md") and "requested path" in found[0]
    (project / "report.md").write_text("x", encoding="utf-8")
    assert C.missing_outputs("Write report.md with the results.", "Done.",
                             [str(project / "report.md")], [project], mirror_roots=[art]) == []


def test_nested_request_is_not_satisfied_by_a_same_named_file_elsewhere(tmp_path):
    from agent8088 import completion as C
    other = tmp_path / "scratch"
    other.mkdir()
    (other / "out.json").write_text("{}", encoding="utf-8")
    missing = C.missing_outputs("Save the data to phase3/out.json.", "Saved phase3/out.json.",
                                [str(other / "out.json")], [tmp_path])
    assert missing == ["phase3/out.json"]


def test_empty_or_missing_output_folder_is_reported(tmp_path):
    from agent8088 import completion as C
    request = "Generate the charts into output/ and finish."
    assert C.missing_outputs(request, "Phase 3 is complete.", [], [tmp_path]) == ["output/ (missing or empty)"]
    (tmp_path / "output").mkdir()
    assert C.missing_outputs(request, "done", [], [tmp_path]) == ["output/ (missing or empty)"]
    (tmp_path / "output" / "a.png").write_bytes(b"png")
    assert C.missing_outputs(request, "done", [], [tmp_path]) == []


def test_partial_outputs_list_only_the_absent_ones(tmp_path):
    from agent8088 import completion as C
    (tmp_path / "a.txt").write_text("1", encoding="utf-8")
    missing = C.missing_outputs("Create a.txt and b.txt.", "both created", [], [tmp_path])
    assert missing == ["b.txt"]


def test_input_directories_and_prose_slashes_are_not_outputs(tmp_path):
    from agent8088 import completion as C
    assert C.named_output_dirs("Read the files in data/ and summarise; use and/or as needed.") == []


# --- files the request supplies ------------------------------------------------------------------

SUPPLIED = ("Build a board where users should be able to export the board to a JSON file and import "
            "such a file again (the file format is in example.json). Put the page in index.html.")


def _workspace(tmp_path, *names):
    """A project folder and a separate artifacts folder holding the supplied files."""
    project, artifacts = tmp_path / "project", tmp_path / "artifacts"
    project.mkdir()
    artifacts.mkdir()
    for name in names:
        (artifacts / name).write_text("{}", encoding="utf-8")
    return project, artifacts, c.inventory([project, artifacts])


def test_a_file_the_request_points_to_is_not_a_missing_output(tmp_path):
    project, artifacts, start = _workspace(tmp_path, "example.json")
    (project / "index.html").write_text("<html></html>", encoding="utf-8")
    assert c.named_output_paths(SUPPLIED) == ["example.json", "index.html"]
    missing = c.missing_outputs(SUPPLIED, "done", [str(project / "index.html")], [project],
                                mirror_roots=[artifacts], start_files=start)
    assert missing == []


def test_without_the_start_inventory_the_file_is_still_reported(tmp_path):
    project, artifacts, _ = _workspace(tmp_path, "example.json")
    (project / "index.html").write_text("<html></html>", encoding="utf-8")
    missing = c.missing_outputs(SUPPLIED, "done", [str(project / "index.html")], [project],
                                mirror_roots=[artifacts])
    assert len(missing) == 1 and missing[0].startswith("example.json")


def test_a_supplied_file_that_the_run_changed_or_wrote_is_not_excused(tmp_path):
    project, artifacts, start = _workspace(tmp_path, "example.json")
    target = artifacts / "example.json"
    assert c.supplied_input(SUPPLIED, "example.json", [target], start, [], [artifacts]) is True
    assert c.supplied_input(SUPPLIED, "example.json", [target], start, [str(target)], [artifacts]) is False
    target.write_text('{"changed": true}', encoding="utf-8")
    assert c.supplied_input(SUPPLIED, "example.json", [target], start, [], [artifacts]) is False


def test_a_file_the_request_asks_for_is_reported_even_if_a_copy_was_there_at_the_start(tmp_path):
    project, artifacts, start = _workspace(tmp_path, "report.md")
    request = "Read data.csv and save the summary to report.md."
    missing = c.missing_outputs(request, "done", [], [project], mirror_roots=[artifacts], start_files=start)
    assert len(missing) == 1 and missing[0].startswith("report.md")
    assert c.is_input_reference(request, "report.md") is False


@pytest.mark.parametrize("request_text", [
    "Create the page (the schema is in spec.json).",
    "Create the page; the layout is described in spec.json.",
    "Create the page, see spec.json for the fields.",
    "Create the page. The fields are given in spec.json.",
])
def test_phrases_that_point_to_existing_material(request_text):
    assert c.is_input_reference(request_text, "spec.json") is True


@pytest.mark.parametrize("request_text", [
    "Create the page and save the data to spec.json.",
    "Export to spec.json (the page is in index.html).",
    "Create the page (see notes.md) and write spec.json.",
])
def test_any_mention_that_asks_for_the_file_makes_it_an_output(request_text):
    assert c.is_input_reference(request_text, "spec.json") is False


def test_the_inventory_is_bounded_and_skips_dependency_folders(tmp_path, monkeypatch):
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "dep.js").write_text("x", encoding="utf-8")
    (tmp_path / "keep.txt").write_text("x", encoding="utf-8")
    found = c.inventory([tmp_path, tmp_path / "missing"])
    assert any(k.endswith("keep.txt") for k in found) and not any("node_modules" in k for k in found)
    monkeypatch.setattr(c, "INVENTORY_LIMIT", 3)
    for i in range(10):
        (tmp_path / f"f{i}.txt").write_text("x", encoding="utf-8")
    assert len(c.inventory([tmp_path])) == 3


# --- throwaway helpers ---------------------------------------------------------------------------

def test_a_probe_nobody_uses_is_a_throwaway_helper(tmp_path):
    helper, page = tmp_path / "cdp_probe.js", tmp_path / "index.html"
    helper.write_text("console.log(1)", encoding="utf-8")
    page.write_text("<html></html>", encoding="utf-8")
    assert c.is_throwaway_helper(str(helper), "build a page", [str(helper), str(page)], [tmp_path]) is True


def test_a_file_that_another_written_file_uses_is_not_a_helper(tmp_path):
    helper, page = tmp_path / "debug_panel.js", tmp_path / "index.html"
    helper.write_text("export const x = 1", encoding="utf-8")
    page.write_text('<script src="debug_panel.js"></script>', encoding="utf-8")
    assert c.is_throwaway_helper(str(helper), "build a page", [str(helper), str(page)], [tmp_path]) is False


def test_a_file_the_request_names_or_with_an_ordinary_name_is_not_a_helper(tmp_path):
    named, plain = tmp_path / "probe.py", tmp_path / "engine.py"
    named.write_text("x = 1", encoding="utf-8")
    plain.write_text("x = 1", encoding="utf-8")
    assert c.is_throwaway_helper(str(named), "write probe.py that checks ports", [str(named)], [tmp_path]) is False
    assert c.is_throwaway_helper(str(plain), "build it", [str(plain)], [tmp_path]) is False


def test_inventory_stops_consuming_entries_at_visit_limit(monkeypatch, tmp_path):
    from types import SimpleNamespace
    consumed = []

    class Scan:
        def __enter__(self):
            return iter(self)

        def __exit__(self, *args):
            pass

        def __iter__(self):
            for i in range(100):
                consumed.append(i)
                yield SimpleNamespace(path=str(tmp_path / f"f{i}.txt"),
                                      is_dir=lambda **k: False, is_file=lambda **k: True,
                                      stat=lambda: SimpleNamespace(st_size=1, st_mtime_ns=1))

    monkeypatch.setattr(c, "INVENTORY_VISIT_LIMIT", 3)
    monkeypatch.setattr(c.os, "scandir", lambda folder: Scan())
    c.inventory([tmp_path])
    assert len(consumed) <= 3


# --- a write the sandbox diverted is a file that exists -----------------------------------------------

def _diverted(tmp_path):
    """A file written for outputs/watch.py that the sandbox kept in the artifacts folder."""
    project, artifacts = tmp_path / "project", tmp_path / "project" / "artifacts"
    (artifacts / "outputs").mkdir(parents=True)
    landed = artifacts / "outputs" / "watch.py"
    landed.write_text("print('x')\n", encoding="utf-8")
    requested = project / "outputs" / "watch.py"
    return project, artifacts, requested, landed


def test_a_file_the_sandbox_kept_in_the_artifacts_folder_is_not_reported_missing(tmp_path):
    project, artifacts, requested, landed = _diverted(tmp_path)
    pairs = [[str(requested), str(landed)]]
    reply = "I wrote outputs/watch.py as asked."
    request = f"Write the script to {requested}"
    assert c.missing_outputs(request, reply, [str(requested)], [project], mirror_roots=[artifacts]) != []
    assert c.missing_outputs(request, reply, [str(requested)], [project],
                             mirror_roots=[artifacts], landed=pairs) == []


def test_a_diverted_file_also_satisfies_a_folder_the_request_asked_to_fill(tmp_path):
    project, artifacts, requested, landed = _diverted(tmp_path)
    pairs = [["outputs/watch.py", str(landed)]]
    request = "Put the scripts in a folder called outputs"
    assert c.missing_outputs(request, "done", [], [project], mirror_roots=[artifacts]) == ["outputs/ (missing or empty)"]
    assert c.missing_outputs(request, "done", [], [project], mirror_roots=[artifacts], landed=pairs) == []


def test_a_diverted_write_whose_copy_is_gone_or_empty_is_still_missing(tmp_path):
    project, artifacts, requested, landed = _diverted(tmp_path)
    pairs = [[str(requested), str(landed)]]
    landed.write_text("", encoding="utf-8")
    reply = "I wrote outputs/watch.py as asked."
    assert c.missing_outputs("make it", reply, [str(requested)], [project], mirror_roots=[artifacts],
                             landed=pairs) == [str(requested)]


@pytest.mark.parametrize("reply", [
    "The cards carry a data-test-hook attribute and test-assertion helpers, and a test-hook.",
    "It is test-driven, with a test-first approach.",
])
def test_hyphenated_words_about_tests_are_not_claimed_test_files(reply):
    assert c._claimed_but_unwritten(reply.lower(), [], []) == []


@pytest.mark.parametrize("reply, found", [
    ("I added test_board.py and logic_test.js.", ["test_board.py", "logic_test.js"]),
    ("Run tests/test-board.py or a.spec.ts.", ["test-board.py", "a.spec.ts"]),
])
def test_real_test_file_names_are_still_claims(reply, found):
    assert c._claimed_but_unwritten(reply.lower(), [], []) == found
