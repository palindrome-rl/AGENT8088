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
