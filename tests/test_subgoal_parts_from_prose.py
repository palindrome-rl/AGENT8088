"""Two ways the "parts of the request" check doubted correct work in the after-fix benchmark run.

1. A prose request (no list) was split at "and then" / ". Then" into chunks that ran across sentences and
   paragraphs, cut off mid-word at 160 characters ("...fix the problem in the module s"). No file can show a
   paragraph of description, so every correct run of that task was told parts were missing.
2. "Create a Python virtual environment in a folder called `.venv` and install the `tabulate` package" starts with
   "Create", so only file text could meet it, and `.venv` is a dependency folder that is never read. The commands
   that made the folder and installed the package did not count, and one correct run spent nine completion asks on it.
"""
from agent8088.completion import extract_subgoals, unaddressed_subgoals

SLOW_BUILD = (
    "This folder has a small build: `python build.py` compiles every module in src/modules and then links them "
    "into out/. The build takes about three minutes.\n\n"
    "Right now the build fails. Please run it, find out why it fails, and fix the problem in the module sources. "
    "Don't change build.py. Then run the full build again so that out/app.bin and out/manifest.json exist from a "
    "successful build.\n\n"
    "When you are done, tell me what was wrong, what you changed and how you checked it.\n")

VENV_PART = ("Create a Python virtual environment in a folder called `.venv` and install the `tabulate` package "
             "from PyPI into it.")
REPORT_PY = "import csv\nimport json\nfrom tabulate import tabulate\n\nrows = list(csv.DictReader(open('sales.csv')))\n"


def test_clauses_that_run_across_sentences_are_not_parts():
    assert extract_subgoals(SLOW_BUILD) == []


def test_a_short_clause_request_still_has_parts():
    parts = extract_subgoals("Write a parser for the log file, then add a summary command that prints the counts.")
    assert len(parts) == 2


def test_a_list_request_is_unchanged():
    parts = extract_subgoals("Please:\n1. Add a login page with a form.\n2. Add a logout button to the header.\n")
    assert parts == ["Add a login page with a form.", "Add a logout button to the header."]


def test_a_part_whose_named_things_were_made_by_commands_is_addressed():
    ran = [r"python -m venv .venv",
           r".venv\Scripts\python.exe -m pip install tabulate && .venv\Scripts\python.exe report.py"]
    assert unaddressed_subgoals([VENV_PART], "", ["report.py", "totals.json"], evidence=REPORT_PY, ran=ran) == []


def test_the_same_part_with_no_venv_command_stays_open():
    ran = [r"python -m pip install tabulate"]
    assert unaddressed_subgoals([VENV_PART], "", ["report.py"], evidence=REPORT_PY, ran=ran) == [VENV_PART]


def test_named_things_in_commands_do_not_meet_a_part_to_build():
    part = "Write `report.py`, which prints a table made with `tabulate`."
    ran = ["echo report.py tabulate"]
    assert unaddressed_subgoals([part], "", [], evidence="", ran=ran) == [part]


def test_a_part_to_build_is_met_when_its_named_things_are_in_the_files():
    part = "Add a `--verbose` flag to `cli.py` that prints each step taken."
    code = "import argparse\nparser.add_argument('--verbose', action='store_true')\n"
    assert unaddressed_subgoals([part], "", ["cli.py"], evidence=code) == []


def test_a_named_flag_missing_from_the_files_stays_open():
    part = "Add a `--verbose` flag to `cli.py` that prints each step taken."
    code = "import argparse\nparser.add_argument('--quiet', action='store_true')\n"
    assert unaddressed_subgoals([part], "", ["cli.py"], evidence=code) == [part]


# 3. In a later benchmark run the sandbox refused writes to `.venv` in the folder, so the model made the venv under
#    artifacts/ and ran `cd artifacts && .venv\Scripts\python.exe ...`. The commands named `.venv`, so the part was
#    met, and the run was called verified while the folder the request asked for did not exist. A path the part names
#    must be where the run is checked, not only somewhere in a command line.

def test_a_named_folder_that_is_not_in_the_project_keeps_the_part_open(tmp_path):
    ran = [r"cd /d artifacts && mkdir .venv\Scripts",
           r"cd /d artifacts && .venv\Scripts\python.exe -m pip install tabulate"]
    assert unaddressed_subgoals([VENV_PART], "", ["report.py"], evidence=REPORT_PY, ran=ran,
                                roots=[tmp_path]) == [VENV_PART]


def test_a_named_folder_in_the_project_still_meets_the_part(tmp_path):
    (tmp_path / ".venv").mkdir()
    ran = [r"python -m venv .venv", r".venv\Scripts\python.exe -m pip install tabulate"]
    assert unaddressed_subgoals([VENV_PART], "", ["report.py"], evidence=REPORT_PY, ran=ran,
                                roots=[tmp_path]) == []


def test_a_named_file_missing_from_the_project_keeps_a_part_to_write_it_open(tmp_path):
    part = "Write `totals.json`, an object that maps each region to its units."
    evidence = "json.dump(totals, open('totals.json', 'w'))  # region units"
    assert unaddressed_subgoals([part], "", ["report.py"], evidence=evidence, roots=[tmp_path]) == [part]
    (tmp_path / "totals.json").write_text("{}")
    assert unaddressed_subgoals([part], "", ["report.py"], evidence=evidence, roots=[tmp_path]) == []


def test_a_part_to_remove_a_named_file_is_not_held_open_by_its_absence(tmp_path):
    part = "Delete `old.log` and remove the logging setup that wrote it."
    ran = ["del old.log"]
    assert unaddressed_subgoals([part], "", ["app.py"], evidence="old log logging setup wrote", ran=ran,
                                roots=[tmp_path]) == []


def test_names_that_are_not_paths_are_not_looked_up_on_disk(tmp_path):
    part = "Install the `tabulate` package and run `npm run build`."
    ran = ["pip install tabulate", "npm run build"]
    assert unaddressed_subgoals([part], "", [], evidence="", ran=ran, roots=[tmp_path]) == []


def test_without_roots_the_check_is_unchanged():
    ran = [r"cd /d artifacts && .venv\Scripts\python.exe -m pip install tabulate"]
    assert unaddressed_subgoals([VENV_PART], "", ["report.py"], evidence=REPORT_PY, ran=ran) == []
