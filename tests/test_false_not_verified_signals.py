"""Finished, working runs that ended NOT VERIFIED on a stale or too literal signal.

Each case was seen in a run whose work passed its grader:
- a `sleep 60 && tail -1 build.log` that found no log stayed "the latest check failed" after the build passed
- a part naming an example value (`$12.34`) stayed open because no file holds the value the page prints
- a part asking for the cart to be "still there after the page is reloaded" stayed open on a page that keeps it in
  localStorage
- a fallback module the run wrote and then deleted itself was reported as a missing file
"""
from agent8088 import completion as cc
from agent8088.trajectory import _is_check_run


def test_reading_a_log_or_waiting_is_not_a_check():
    assert not _is_check_run('sleep 60 && tail -1 build.log && echo "still running"')
    assert not _is_check_run("head -5 build/output.txt")
    assert not _is_check_run("kill -0 3 2>/dev/null")


def test_a_real_build_or_test_is_still_a_check():
    assert _is_check_run("python build.py > build.log 2>&1")
    assert _is_check_run("make 2>&1 | tail -20")
    assert _is_check_run("pytest -q && tail -1 report.txt")


PRICES = ("Elements with ids `subtotal`, `discount` and `total` show amounts in dollars with two decimals, "
          "like `$12.34`. The total is the subtotal minus the discount.")
PAGE = ('<span id="subtotal"></span><span id="discount"></span><span id="total"></span>'
        '<script>function fmt(c){return "$"+Math.floor(c/100)+"."+String(c%100).padStart(2,"0")}</script>')


def test_an_example_value_is_not_a_name_the_files_must_hold(tmp_path):
    page = tmp_path / "shop.html"
    page.write_text(PAGE, encoding="utf-8")
    assert cc.unaddressed_subgoals([PRICES], "Done.", [str(page)], evidence=PAGE, roots=[tmp_path]) == []


def test_the_named_ids_must_still_be_there(tmp_path):
    page = tmp_path / "shop.html"
    without = PAGE.replace('id="discount"', 'id="off"').replace('id="total"', 'id="sum"')
    page.write_text(without, encoding="utf-8")
    assert cc.unaddressed_subgoals([PRICES], "Done.", [str(page)], evidence=without, roots=[tmp_path]) == [PRICES]


def test_a_named_thing_with_letters_after_like_still_counts():
    assert cc._named_things("a flag, like `--verbose`") == ["--verbose"]
    assert cc._named_things("amounts like `$12.34` in `total`") == ["total"]


RELOAD = "The cart and an applied code are still there after the page is reloaded."


def test_state_kept_across_a_reload_is_met_by_browser_storage(tmp_path):
    page = tmp_path / "shop.html"
    text = "<script>function saveState(){localStorage.setItem('cart', JSON.stringify(cart))}</script>"
    page.write_text(text, encoding="utf-8")
    assert cc.unaddressed_subgoals([RELOAD], "Done.", [str(page)], evidence=text, roots=[tmp_path]) == []


def test_without_storage_the_reload_part_stays_open(tmp_path):
    page = tmp_path / "shop.html"
    text = "<script>let cart = {};</script>"
    page.write_text(text, encoding="utf-8")
    assert cc.unaddressed_subgoals([RELOAD], "Done.", [str(page)], evidence=text, roots=[tmp_path]) == [RELOAD]


def test_a_file_the_run_deleted_itself_is_not_missing(tmp_path):
    (tmp_path / "report.py").write_text("print(1)\n", encoding="utf-8")
    shim = tmp_path / "artifacts" / "tabulate_shim.py"
    reply = "report.py works. tabulate_shim.py was a fallback; I deleted it once the real package installed."
    written = [str(tmp_path / "report.py"), str(shim)]
    assert cc.missing_outputs("Write report.py.", reply, written, [tmp_path]) == [str(shim)]
    removed = cc.removed_by_commands([f'cd "{tmp_path}" && rm -f artifacts/tabulate_shim.py && echo removed'])
    assert removed == {"tabulate_shim.py"}
    assert cc.missing_outputs("Write report.py.", reply, written, [tmp_path], removed=removed) == []


def test_a_named_deliverable_the_run_deleted_is_still_missing(tmp_path):
    removed = cc.removed_by_commands(["rm report.py"])
    assert cc.missing_outputs("Write `report.py`.", "Done.", [str(tmp_path / "report.py")], [tmp_path],
                              removed=removed) == ["report.py"]
