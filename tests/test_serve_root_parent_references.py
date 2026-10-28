"""The page check serves the ancestor folder that a page's `../` references reach, not only for src= and href=.

Seen in a demo run: test/harness.html imported "../js/catalog.js" in a module script. serve_root only counted src and
href attributes, so the page was served from test/, every import answered 404, and the page was reported broken."""
import pytest

from agent8088 import app_check
from tests.test_app_check import chromium, needs_chromium  # noqa: F401


def _page(tmp_path, body, folder="test"):
    (tmp_path / folder).mkdir(exist_ok=True)
    page = tmp_path / folder / "harness.html"
    page.write_text(body, encoding="utf-8")
    return page


@pytest.mark.parametrize("body", [
    '<script type="module">import { a } from "../js/a.js";</script>',
    "<script type=module>import {a} from '../js/a.js'</script>",
    '<script type="module">import "../js/a.js";</script>',
    '<script type="module">const m = await import("../js/a.js");</script>',
    '<script type="module">export * from "../js/a.js";</script>',
    '<script type="module">const r = await fetch("../data/x.json");</script>',
    '<script type="module">const u = new URL("../img/a.png", import.meta.url);</script>',
    '<style>body { background: url(../img/bg.png); }</style>',
    '<style>body { background: url("../img/bg.png"); }</style>',
    '<img src=../img/a.png>',
    '<link rel="stylesheet" href="./../style.css">',
])
def test_a_parent_reference_moves_the_served_folder_up(tmp_path, body):
    assert app_check.serve_root(_page(tmp_path, body)) == tmp_path


def test_two_levels_up_is_followed(tmp_path):
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    deep = nested / "p.html"
    deep.write_text('<script type="module">import "../../js/a.js";</script>', encoding="utf-8")
    assert app_check.serve_root(deep) == tmp_path


@pytest.mark.parametrize("body", [
    '<script type="module">import { a } from "./js/a.js";</script>',
    '<script type="module">import { a } from "js/a.js";</script>',
    '<script src="js/a.js"></script><p>from the top</p>',
    '<p>see ../notes for details</p>',
    '<script>const x = "../not-a-reference";</script>',
])
def test_pages_that_stay_in_their_folder_are_served_from_it(tmp_path, body):
    page = _page(tmp_path, body)
    assert app_check.serve_root(page) == page.parent


@needs_chromium
def test_a_page_whose_module_imports_from_the_parent_folder_passes(tmp_path):
    (tmp_path / "js").mkdir()
    (tmp_path / "js" / "a.js").write_text("export const answer = 42;\n", encoding="utf-8")
    page = _page(tmp_path, '<!doctype html><html><body><button id="go">Go</button><p id="out">waiting</p>'
                           '<script type="module">import { answer } from "../js/a.js";'
                           'document.getElementById("go").onclick = () => {'
                           'document.getElementById("out").textContent = "answer " + answer; };'
                           '</script></body></html>')
    report = app_check.check_web_page(page)
    assert not report.failed, report.failures
    assert report.status == app_check.PASS
