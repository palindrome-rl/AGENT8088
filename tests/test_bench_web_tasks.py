"""The game, page and scraper task graders pass reference solutions and fail broken ones.

Each broken solution is one a run could leave behind and still call done: a flip that also turns the diagonals, a
score kept only in memory, a discount truncated instead of rounded, a scraper that only knows the given pages or
counts the suggested box. The page graders need a browser and are skipped where there is none.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TASKS = ROOT / "bench" / "tasks"
REFS = Path(__file__).resolve().parent / "bench_references"
WEB_TASKS = ("tile-flip-game", "three-in-row-game", "cart-page", "catalog-scrape")


def _browser_env():
    env = dict(os.environ)
    shared = Path(os.environ.get("LOCALAPPDATA", "")) / "agent8088" / "playwright-browsers" \
        if sys.platform == "win32" else Path.home() / ".agent8088" / "playwright-browsers"
    if "PLAYWRIGHT_BROWSERS_PATH" not in env and shared.is_dir():
        env["PLAYWRIGHT_BROWSERS_PATH"] = str(shared)
    return env


def _grade(task: str, workspace: Path, tmp_path: Path, env=None) -> dict:
    out = tmp_path / f"grade-{task}.json"
    subprocess.run([sys.executable, str(TASKS / task / "grade.py"), str(workspace), "--json", str(out)],
                   check=True, capture_output=True, timeout=600, env=env or _browser_env())
    return json.loads(out.read_text(encoding="utf-8"))


def _workspace(task: str, tmp_path: Path, name: str = "ws") -> Path:
    workspace = tmp_path / name
    fixture = TASKS / task / "fixture"
    if fixture.is_dir():
        shutil.copytree(fixture, workspace)
    else:
        workspace.mkdir()
    return workspace


def _with_reference(task: str, tmp_path: Path, edit=None, name: str = "ws") -> Path:
    workspace = _workspace(task, tmp_path, name)
    for source in (REFS / task).iterdir():
        text = source.read_text(encoding="utf-8")
        if edit:
            changed = edit(text)
            assert changed != text, "the broken variant must change the reference"
            text = changed
        (workspace / source.name).write_text(text, encoding="utf-8")
    return workspace


@pytest.fixture(scope="module")
def browser(tmp_path_factory):
    probe = tmp_path_factory.mktemp("probe")
    (probe / "game.html").write_text("<p>x</p>", encoding="utf-8")
    report = _grade("tile-flip-game", probe, probe)
    if "error" in report:
        pytest.skip(report["error"])


def test_the_web_tasks_are_listed_and_have_references():
    for task in WEB_TASKS:
        meta = json.loads((TASKS / task / "task.json").read_text(encoding="utf-8"))
        assert meta["id"] == task and meta["kind"] == "sandbox" and meta["budget_sec"] == 900
        assert (TASKS / task / meta["prompt"]).is_file() and (REFS / task).is_dir()


@pytest.mark.parametrize("task", WEB_TASKS)
def test_an_empty_workspace_fails(task, tmp_path):
    report = _grade(task, _workspace(task, tmp_path), tmp_path)
    assert report.get("passed") is not True


@pytest.mark.parametrize("task", ["tile-flip-game", "three-in-row-game", "cart-page"])
def test_the_reference_page_passes(task, tmp_path, browser):
    report = _grade(task, _with_reference(task, tmp_path), tmp_path)
    assert report["passed"], report


# ----------------------------------------------------------------- tile-flip-game

def test_tile_flip_fails_a_flip_that_turns_the_diagonals_too(tmp_path, browser):
    workspace = _with_reference("tile-flip-game", tmp_path, lambda s: s.replace(
        "[r, c + 1]]", "[r, c + 1], [r - 1, c - 1], [r + 1, c + 1], [r - 1, c + 1], [r + 1, c - 1]]"))
    report = _grade("tile-flip-game", workspace, tmp_path)
    assert not report["passed"]
    assert not report["checks"]["edge and corner clicks follow the rules"]


def test_tile_flip_fails_a_page_that_ignores_the_start_address(tmp_path, browser):
    workspace = _with_reference("tile-flip-game", tmp_path, lambda s: s.replace(
        '.get("start")', '.get("ignored")'))
    report = _grade("tile-flip-game", workspace, tmp_path)
    assert not report["passed"]
    assert not report["checks"]["edge and corner clicks follow the rules"]


def test_tile_flip_fails_clicks_counted_after_solving(tmp_path, browser):
    workspace = _with_reference("tile-flip-game", tmp_path, lambda s: s.replace("if (solved()) return;", ""))
    report = _grade("tile-flip-game", workspace, tmp_path)
    assert not report["checks"]["clicks after solving change nothing"]


# ----------------------------------------------------------------- three-in-row-game

def test_three_in_row_fails_a_score_kept_only_in_memory(tmp_path, browser):
    workspace = _with_reference("three-in-row-game", tmp_path, lambda s: s.replace(
        'localStorage.getItem(KEY) || "{}"', '"{}"'))
    report = _grade("three-in-row-game", workspace, tmp_path)
    assert not report["passed"]
    assert not report["checks"]["the scores survive a reload"]


def test_three_in_row_fails_a_board_that_misses_diagonals(tmp_path, browser):
    workspace = _with_reference("three-in-row-game", tmp_path, lambda s: s.replace(",[0,4,8],[2,4,6]", ""))
    report = _grade("three-in-row-game", workspace, tmp_path)
    assert not report["passed"]
    assert not report["checks"]["a diagonal wins"]


def test_three_in_row_fails_a_page_that_loads_from_the_internet(tmp_path, browser):
    workspace = _with_reference("three-in-row-game", tmp_path, lambda s: s.replace(
        "<title>", '<script src="https://cdn.example.com/x.js"></script><title>'))
    report = _grade("three-in-row-game", workspace, tmp_path)
    assert not report["passed"]
    assert not report["checks"]["no console errors or outside requests"]


# ----------------------------------------------------------------- cart-page

def test_cart_fails_a_discount_truncated_instead_of_rounded(tmp_path, browser):
    workspace = _with_reference("cart-page", tmp_path, lambda s: s.replace(
        "Math.floor((subtotal * 10 + 50) / 100)", "Math.floor(subtotal / 10)"))
    report = _grade("cart-page", workspace, tmp_path)
    assert not report["passed"]
    assert not report["checks"]["10% off at $50.45, half a cent rounded up"]


def test_cart_fails_a_cart_lost_on_reload(tmp_path, browser):
    workspace = _with_reference("cart-page", tmp_path, lambda s: s.replace(
        'localStorage.getItem(KEY) || "{}"', '"{}"'))
    report = _grade("cart-page", workspace, tmp_path)
    assert not report["passed"]
    assert not report["checks"]["the cart and the code survive a reload"]


def test_cart_fails_a_discount_that_stays_below_the_line(tmp_path, browser):
    workspace = _with_reference("cart-page", tmp_path, lambda s: s.replace(
        'state.code === "SAVE10" && subtotal >= 5000', 'state.code === "SAVE10"'))
    report = _grade("cart-page", workspace, tmp_path)
    assert not report["passed"]
    assert not report["checks"]["no discount below $50.00"]


def test_a_page_grader_without_a_browser_reports_ungraded(tmp_path):
    workspace = _with_reference("cart-page", tmp_path)
    env = {**os.environ, "PLAYWRIGHT_BROWSERS_PATH": str(tmp_path / "no-browsers-here")}
    report = _grade("cart-page", workspace, tmp_path, env=env)
    assert "passed" not in report
    assert "browser" in report["error"]


# ----------------------------------------------------------------- catalog-scrape

def test_scrape_passes_the_reference(tmp_path):
    report = _grade("catalog-scrape", _with_reference("catalog-scrape", tmp_path), tmp_path)
    assert report["passed"], report


def test_scrape_fails_rows_written_for_the_given_pages_only(tmp_path):
    from importlib import util
    spec = util.spec_from_file_location("shop", TASKS / "catalog-scrape" / "shop.py")
    shop = util.module_from_spec(spec)
    spec.loader.exec_module(shop)
    rows = [["sku", "name", "price", "in_stock"], *shop.expected_rows(shop.FIXTURE)]
    workspace = _workspace("catalog-scrape", tmp_path)
    (workspace / "scrape.py").write_text(
        f"import csv\nwith open('products.csv', 'w', newline='') as f:\n    csv.writer(f).writerows({rows!r})\n",
        encoding="utf-8")
    report = _grade("catalog-scrape", workspace, tmp_path)
    assert report["checks"]["every row is right on the given shop"]
    assert not report["passed"]
    assert not report["checks"]["every row is right on the second shop"]


def test_scrape_fails_a_scraper_that_counts_the_suggested_box(tmp_path):
    workspace = _with_reference("catalog-scrape", tmp_path, lambda s: s.replace(
        'if self._in_results and tag == "li"', 'if tag == "li"'))
    report = _grade("catalog-scrape", workspace, tmp_path)
    assert not report["passed"]
    assert not report["checks"]["every row is right on the given shop"]


def test_scrape_fails_the_struck_out_price(tmp_path):
    workspace = _with_reference("catalog-scrape", tmp_path, lambda s: s.replace(
        'not (self._field == "price" and self._struck)', "True").replace(".split()[-1]", ".split()[0]"))
    report = _grade("catalog-scrape", workspace, tmp_path)
    assert not report["passed"]
    assert not report["checks"]["every row is right on the given shop"]


def test_scrape_fails_a_package_from_outside_the_standard_library(tmp_path):
    workspace = _with_reference("catalog-scrape", tmp_path, lambda s: s.replace(
        "import csv\n", "import csv\ntry:\n    import requests  # noqa: F401\nexcept ImportError:\n    pass\n", 1))
    report = _grade("catalog-scrape", workspace, tmp_path)
    assert not report["passed"]
    assert not report["checks"]["uses only the standard library"]


def test_scrape_fails_a_scraper_that_stops_at_the_first_page(tmp_path):
    workspace = _with_reference("catalog-scrape", tmp_path, lambda s: s.replace(
        "url = urljoin(url, page.next) if page.next else None", "url = None"))
    report = _grade("catalog-scrape", workspace, tmp_path)
    assert not report["passed"]
    assert not report["checks"]["every row is right on the second shop"]


# ----------------------------------------------------------------- the runner lists them

def test_the_runner_lists_the_tasks_without_a_profile():
    listed = subprocess.run([sys.executable, str(ROOT / "bench" / "run.py"), "--list"], capture_output=True,
                            text=True, timeout=60, check=False)
    assert listed.returncode == 0, listed.stderr
    for task in WEB_TASKS:
        assert task in listed.stdout


def test_the_runner_still_needs_a_profile_to_run():
    run = subprocess.run([sys.executable, str(ROOT / "bench" / "run.py"), "--task", "cart-page"],
                         capture_output=True, text=True, timeout=60, check=False)
    assert run.returncode == 2 and "--profile" in run.stderr
