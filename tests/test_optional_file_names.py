"""A file the request only asks for under a condition, or as a choice, is not a file the run has to produce.

Seen in a run: a request ended with a sentence asking for a notes file only if the work ran long, and the run was
told a required file was missing, because any file name after an output verb was counted. The work had not run
long, so it had no reason to write one, and the answer carried "missing or empty files" and a NOT VERIFIED headline.

What is skipped: a file in the main clause of a sentence that opens with a condition ("If ...,", "Unless ..."), and a
file whose verb is offered as a choice ("optionally write", "you may also save", "feel free to create"). What stays
required: a plain instruction, a time clause ("When done, write ..."), a condition in the middle of a sentence that
does not govern the file ("Read the log if present and write ..."), and "unless it already exists", which a file
that is there already meets.
"""
from agent8088 import completion as cc


def names(text):
    return cc.named_output_paths(text)


def test_a_file_after_an_opening_condition_is_optional():
    assert names("If the session runs long, save a short NOTES.md with the choices made so far.") == []
    assert names("Unless it is already there, create config.yaml.") == []
    assert names("In case the run is interrupted, write progress.json.") == []
    assert names("If needed write helper.py.") == []


def test_a_file_the_request_offers_as_a_choice_is_optional():
    assert names("Optionally write NOTES.md.") == []
    assert names("You may also save extra.md.") == []
    assert names("Feel free to create sketch.txt.") == []
    assert names("You can also write summary.html.") == []


def test_a_long_conditional_sentence_names_nothing_required():
    text = ("If the session runs long, write a brief STATUS.md in the project folder listing the choices made, "
            "what is finished and what is left.")
    assert names(text) == []


def test_the_required_files_beside_an_optional_one_stay_required():
    assert names("Build the tool in tool.py. If the session runs long, write a brief STATUS.md.") == [
        "tool.py"]
    assert names("Write the answer to out.txt. If the run is long, also save a short notes.md.") == ["out.txt"]


def test_plain_instructions_are_still_required():
    assert names("Create hello.py and run it.") == ["hello.py"]
    assert names("Write the answer to out.txt.") == ["out.txt"]
    assert names("Save the report as report.md and the data as data.csv.") == ["report.md", "data.csv"]


def test_a_time_clause_does_not_make_a_file_optional():
    assert names("When done, write summary.md.") == ["summary.md"]
    assert names("Once the build finishes, save build.log.") == ["build.log"]


def test_a_condition_in_the_middle_of_a_sentence_does_not_govern_the_file():
    assert names("Read the log if present and write summary.md.") == ["summary.md"]
    assert names("Write results.json, using the cache if it exists.") == ["results.json"]


def test_a_file_that_is_required_unless_it_exists_is_still_named():
    assert names("Save the results to results.json unless it already exists.") == ["results.json"]


def test_a_file_not_written_is_not_missing_when_the_request_made_it_optional(tmp_path):
    request = "Build the tool in tool.py. If the session runs long, write a brief STATUS.md."
    (tmp_path / "tool.py").write_text("print('x')\n", encoding="utf-8")
    assert cc.missing_outputs(request, "", ["tool.py"], [tmp_path]) == []


def test_a_required_file_that_is_not_there_is_still_missing(tmp_path):
    request = "Build the tool in tool.py. If the session runs long, write a brief STATUS.md."
    assert cc.missing_outputs(request, "", [], [tmp_path]) != []
