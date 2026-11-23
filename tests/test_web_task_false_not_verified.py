"""Two false NOT VERIFIED results from the scraper benchmark task. The script was right and the grader passed it.

1. "It writes `products.csv` in the current folder with the header ..." made the harness look for a folder called
   `with/`: the "folder <name>" pattern took the next word, whatever it was. The run was told to create `with/` and
   ended NOT VERIFIED with "missing or empty files: with/".
2. The smoke test starts every script with `--help`. The scraper takes the shop's address as its first argument, so it
   tried to fetch "--help/products/page1.html" and stopped with "ValueError: unknown url type". The run reported "the
   page/script check failed (scrape.py)", though the same script had just run correctly against the real server.
"""
import pytest

from agent8088 import app_check
from agent8088 import completion as c


@pytest.mark.parametrize("sentence", [
    "It writes `products.csv` in the current folder with the header `sku,name`.",
    "Save it in the folder that the script runs from.",
    "Keep everything in this directory and the tests in it.",
    "Write the files to the folder for the build.",
])
def test_a_plain_word_after_folder_is_not_a_folder_name(sentence):
    assert c.named_output_dirs(sentence) == []


@pytest.mark.parametrize("sentence, folder", [
    ("Put the scripts in a folder called outputs.", "outputs"),
    ("Write the report into a folder named results.", "results"),
    ("Save the files in a folder called `with`.", "with"),
])
def test_a_folder_the_request_names_is_still_found(sentence, folder):
    assert c.named_output_dirs(sentence) == [folder]


SCRAPER_START = ("Traceback (most recent call last):\n  File \"scrape.py\", line 98, in <module>\n"
                 "  File \"urllib/request.py\", line 378, in _parse\n"
                 "ValueError: unknown url type: '--help/products/page1.html'\nCommand exited with status 1.")


def test_a_script_that_took_help_as_its_input_did_not_crash(tmp_path):
    script = tmp_path / "scrape.py"
    script.write_text("import sys\n", encoding="utf-8")
    soft = []
    assert app_check._run_briefly(script, lambda command, timeout: SCRAPER_START, soft=soft) == []
    assert soft and "--help" in soft[0] and "not verified" in soft[0]


def test_a_script_that_took_help_as_its_input_is_not_a_failed_check(tmp_path):
    script = tmp_path / "scrape.py"
    script.write_text("import sys\n", encoding="utf-8")
    report = app_check.check_script(script, run=lambda command, timeout: SCRAPER_START)
    assert not report.failed


def test_a_crash_in_the_code_is_still_a_crash(tmp_path):
    script = tmp_path / "scrape.py"
    script.write_text("import sys\n", encoding="utf-8")
    crashed = ("Traceback (most recent call last):\n  File \"scrape.py\", line 3, in <module>\n"
               "NameError: name 'urljoin' is not defined\nCommand exited with status 1.")
    assert app_check._run_briefly(script, lambda command, timeout: crashed, soft=[])
    assert app_check.check_script(script, run=lambda command, timeout: crashed).failed


# 3. The page check clicks the first control and fails the page when nothing changes. On four game pages from the
#    benchmark, the first control was "New game" or "Restart"; on a fresh board that changes nothing, by design, and
#    each page that passed every grader check was reported "the page/script check failed".
from tests.test_app_check import _page, chromium, needs_chromium  # noqa: F401

FRESH_GAME = """<!doctype html><html><body>
<button id="{id}">{label}</button>
<div id="board"><button class="cell" data-cell="0"></button><button class="cell" data-cell="1"></button></div>
<p id="status">X to move</p>
<script>
let turn = 'X';
const fresh = () => { document.querySelectorAll('.cell').forEach(c => c.textContent = '');
  turn = 'X'; document.getElementById('status').textContent = 'X to move'; };
document.getElementById('{id}').onclick = fresh;
document.querySelectorAll('.cell').forEach(c => c.onclick = () => { if (!c.textContent) { c.textContent = turn;
  turn = turn === 'X' ? 'O' : 'X'; document.getElementById('status').textContent = turn + ' to move'; } });
</script></body></html>"""


@needs_chromium
@pytest.mark.parametrize("control_id, label", [
    ("new-game", "New game"), ("restart", "Restart"), ("again", "Play again"), ("over", "Start over"),
])
def test_a_start_again_control_on_a_fresh_board_is_not_dead(tmp_path, control_id, label):
    report = app_check.check_web_page(_page(tmp_path, FRESH_GAME.replace("{id}", control_id).replace("{label}", label)))
    assert not any("changed nothing" in f for f in report.failures), report.failures


@needs_chromium
def test_a_dead_play_button_still_fails(tmp_path):
    dead = "<!doctype html><html><body><button id='play'>Play</button><p id='out'>waiting</p></body></html>"
    report = app_check.check_web_page(_page(tmp_path, dead))
    assert report.failed and any("changed nothing" in f for f in report.failures)


# 4. A check whose test tool is not installed here did not fail. On a game page from the benchmark, the model's own
#    browser test stopped at `import playwright` (the shell it ran in has no Playwright), and the run ended "the latest
#    check failed" although the page passed every grader check. A package the work itself needs, or a module of the
#    project, is still a failure.
from agent8088.trajectory import TrajectoryState


def _ran(command, result, written=("test_board.py", "app.py")):
    state = TrajectoryState({}, goal="g", message_count=1)
    state.data["written_paths"] = [f"E:\\work\\{name}" for name in written]
    operation = state.before_tool("execute_shell")
    state.after_tool(operation, result, failed=False, blocked=False, mutated=False, verdict="failed", command=command)
    return state


NO_PLAYWRIGHT = ("Traceback (most recent call last):\n  File \"<string>\", line 1, in <module>\n"
                 "ModuleNotFoundError: No module named 'playwright'\nCommand exited with status 1.")


@pytest.mark.parametrize("command, output, tool", [
    ("python -c \"import playwright\" && python test_board.py", NO_PLAYWRIGHT, "playwright"),
    ("python test_board.py", ("  File \"test_board.py\", line 2\nModuleNotFoundError: No module named "
                              "'selenium.webdriver'\nCommand exited with status 1."), "selenium"),
    ("node test_board.js", ("Error: Cannot find module 'puppeteer'\nRequire stack:\n- E:\\work\\test_board.js\n"
                            "Command exited with status 1."), "puppeteer"),
    ("node test_board.js", ("Error [ERR_MODULE_NOT_FOUND]: Cannot find package '@playwright/test' imported from "
                            "E:\\work\\test_board.js\nCommand exited with status 1."), "@playwright/test"),
    ("python -m pytest -q", (r"C:\Python312\python.exe: No module named pytest"
                             "\nCommand exited with status 1."), "pytest"),
])
def test_a_check_whose_test_tool_is_missing_did_not_fail(command, output, tool):
    state = _ran(command, output)
    assert not state.check_failing()
    assert tool in state.environment_note()


@pytest.mark.parametrize("output", [
    "ModuleNotFoundError: No module named 'requests'\nCommand exited with status 1.",
    "ModuleNotFoundError: No module named 'board_logic'\nCommand exited with status 1.",
    "Error: Cannot find module './board.js'\nCommand exited with status 1.",
])
def test_a_missing_package_the_work_needs_is_still_a_failure(output):
    assert _ran("python test_board.py", output).check_failing()


def test_a_test_that_failed_after_importing_its_tool_is_still_a_failure():
    output = "1 failed, 3 passed in 2.1s\nModuleNotFoundError: No module named 'playwright'\nCommand exited with status 1."
    assert _ran("python -m pytest -q", output).check_failing()
