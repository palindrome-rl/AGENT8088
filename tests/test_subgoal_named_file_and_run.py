"""A part that names a file it asks for, and a part that asks to run what was made, are met by the run's own facts.

Seen in a short run: a request to "create a file X that prints ..., then run it to check the output" was done and
passed its own check, yet both parts stayed "not addressed" and the run ended NOT VERIFIED. The first part's words
("create", "file", "numbers", "line") are words about the act of making a file or about what the code prints, and
none of them is in the code. The second part ("run it to check the output") names nothing a command line could
contain. Both parts stayed open until the completion checks ran out.

What is checked here: the file the part names is written and not empty, so the words that only restate "make a
file" count as met; and a part that asks to run "it" is met by a command that ran a file this run wrote. A part
that asks for more than that (behaviour, a named result) still has to be shown by the files, and a command that
only reads a file or echoes its name never counts.
"""
from agent8088 import completion as cc

CODE = "for n in range(1, 6):\n    print(n * n)\n"
PARTS = [
    "Create a file squares.py that prints the square of each number from 1 to 5, one per line",
    "run it to check the output",
]


def test_the_named_file_and_the_run_of_it_meet_both_parts():
    assert cc.unaddressed_subgoals(PARTS, "", ["squares.py"], evidence=CODE, ran=["python squares.py"]) == []


def test_the_same_holds_when_the_file_is_found_in_a_project_folder(tmp_path):
    (tmp_path / "squares.py").write_text(CODE, encoding="utf-8")
    open_parts = cc.unaddressed_subgoals(
        PARTS, "", ["squares.py"], evidence=CODE, ran=["python squares.py"], roots=[tmp_path])
    assert open_parts == []


def test_an_empty_file_is_not_the_file_that_was_asked_for(tmp_path):
    (tmp_path / "squares.py").write_text("", encoding="utf-8")
    open_parts = cc.unaddressed_subgoals(
        PARTS[:1], "", ["squares.py"], evidence=" ", ran=[], roots=[tmp_path])
    assert open_parts == PARTS[:1]


def test_a_file_that_was_not_written_does_not_meet_the_part():
    assert cc.unaddressed_subgoals(PARTS[:1], "", [], evidence=" ") == PARTS[:1]
    assert cc.unaddressed_subgoals(PARTS[:1], "", ["other.py"], evidence=CODE) == PARTS[:1]


def test_the_file_alone_does_not_meet_a_part_that_asks_for_more_than_a_file():
    part = ["Create a file server.py that implements login, logout, signup and password reset"]
    assert cc.unaddressed_subgoals(part, "", ["server.py"], evidence="import http.server\n") == part


def test_the_file_meets_the_part_when_its_other_words_are_in_it():
    part = ["Create a file server.py that implements login, logout, signup and password reset"]
    code = "def login(): pass\ndef logout(): pass\ndef signup(): pass\ndef password_reset(): pass\n"
    assert cc.unaddressed_subgoals(part, "", ["server.py"], evidence=code) == []


def test_a_file_the_part_only_reads_is_not_a_file_it_asks_for():
    part = ["Read data.csv and print the total of each column"]
    assert cc.unaddressed_subgoals(part, "", ["data.csv"], evidence="a,b\n1,2\n") == part


def test_running_it_needs_a_command_that_ran_a_written_file():
    part = PARTS[1:]
    assert cc.unaddressed_subgoals(part, "", ["squares.py"], evidence=CODE, ran=[]) == part
    assert cc.unaddressed_subgoals(part, "", ["squares.py"], evidence=CODE, ran=["python other.py"]) == part


def test_reading_or_echoing_a_file_is_not_running_it():
    part = PARTS[1:]
    for command in ("cat squares.py", "type squares.py", "echo squares.py", "grep print squares.py",
                    "Get-Content squares.py"):
        assert cc.unaddressed_subgoals(part, "", ["squares.py"], evidence=CODE, ran=[command]) == part, command


def test_other_ways_to_say_run_it_and_check_it():
    for part in ("Execute it and verify the result.", "Then run squares.py and confirm the output is right.",
                 "Run the script and make sure it works."):
        assert cc.unaddressed_subgoals([part], "", ["squares.py"], evidence=CODE,
                                       ran=["python squares.py"]) == [], part


def test_a_part_that_names_a_result_is_not_met_by_running_it():
    part = ["Run it and confirm the report lists twelve customers"]
    assert cc.unaddressed_subgoals(part, "", ["squares.py"], evidence=CODE, ran=["python squares.py"]) == part


def test_running_something_else_is_still_met_the_old_way():
    part = ["Run `npm run build`, then run `node dist/app.js` and check what it prints."]
    ran = ["npm run build", "node dist/app.js"]
    assert cc.unaddressed_subgoals(part, "", ["package.json"], evidence="{}", ran=ran) == []


def test_a_forgotten_part_is_still_found_beside_a_met_one():
    parts = PARTS + ["Add a README with install instructions"]
    assert cc.unaddressed_subgoals(parts, "", ["squares.py"], evidence=CODE,
                                   ran=["python squares.py"]) == parts[2:]
