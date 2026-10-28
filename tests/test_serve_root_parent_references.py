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


@pytest.mark.parametrize("body", [
    '<p>Read from "../notes" for details.</p>',
    '<!-- <script>fetch("../notes")</script> -->',
    '<script>const example = \'import "../notes"\';</script>',
    '<script>// import "../notes"\nconst x = 1;</script>',
    '<script>/* fetch("../notes") */ const x = 1;</script>',
    '<script>const pattern = /from "..\\/notes"/;</script>',
    '<script type="application/json">{"example":"from ../notes"}</script>',
    '<style>p::before { content: \'url(../notes)\'; }</style>',
    '<script>new URL("../img/a.png", "https://example.com/base/");</script>',
    '<base href="https://example.com/"><script>fetch("../data.json")</script>',
    '<base href="/"><script>fetch("../data.json")</script>',
    '<template><script type="module">import "../example.js"</script></template>',
    '<noscript><link href="../no-js.css"></noscript>',
])
def test_text_and_non_page_relative_urls_do_not_expand_the_root(tmp_path, body):
    page = _page(tmp_path, body)
    assert app_check.serve_root(page) == page.parent


@pytest.mark.parametrize("body", [
    '<script type="module">import ".././../js/a.js";</script>',
    '<script>importScripts("../a.js", "../../b.js");</script>',
    '<script type="module">const x=1 / 2; import "../../js/a.js";</script>',
    '<script>fetch(`../../data.json`);</script>',
    '<div style="background:url(../../img/a.png)"></div>',
    '<base href="../assets/"><script>fetch("../../data.json")</script>',
])
def test_all_literal_arguments_and_normalized_parent_segments_are_counted(tmp_path, body):
    folder = tmp_path / 'a' / 'b'
    folder.mkdir(parents=True)
    page = folder / 'index.html'
    page.write_text(body, encoding='utf-8')
    assert app_check.serve_root(page) == tmp_path


def test_parent_climb_is_still_bounded_to_three_levels(tmp_path):
    folder = tmp_path / 'a' / 'b' / 'c' / 'd'
    folder.mkdir(parents=True)
    page = folder / 'index.html'
    page.write_text('<script>fetch("../../../../../../data.json")</script>', encoding='utf-8')
    assert app_check.serve_root(page) == tmp_path / 'a'


@needs_chromium
def test_prose_does_not_break_a_working_root_relative_script(tmp_path):
    page = _page(tmp_path, '<html><body><p>Read from "../notes" for details.</p>'
                 '<button id="go">Go</button><p id="out">waiting</p><script src="/app.js"></script></body></html>')
    (page.parent / 'app.js').write_text(
        "document.getElementById('go').onclick=()=>document.getElementById('out').textContent='clicked';",
        encoding='utf-8')
    report = app_check.check_web_page(page, click_steps=0)
    assert report.status == app_check.PASS, report.text()


@needs_chromium
@pytest.mark.parametrize('automatic', [False, True])
def test_page_assets_cannot_read_outside_allowed_paths(engine, monkeypatch, tmp_path, automatic):
    from agent8088.trajectory import TrajectoryState
    project = tmp_path / 'project'
    project.mkdir()
    page = project / 'index.html'
    (tmp_path / 'neighbour.txt').write_text('OUTSIDE_SCOPE_FIXTURE', encoding='utf-8')
    page.write_text('<html><body><script>fetch("../neighbour.txt").then(r=>r.text())'
                    '.then(t=>console.error(t));</script></body></html>', encoding='utf-8')
    monkeypatch.setattr(engine, 'PROJECT_ROOT', project)
    monkeypatch.setattr(engine, 'ARTIFACTS_ROOT', project / 'artifacts')
    monkeypatch.setattr(engine, 'ALLOWED_PATHS', [project])
    monkeypatch.setattr(engine, 'PERMISSION_MODE', 'full-auto')
    if automatic:
        state = TrajectoryState({}, goal='Build a web page', message_count=1)
        state.data['written_paths'] = [str(page)]
        state.data['mutation_count'] = 1
        engine._smoke_test_step(state, [str(page)], report_only=True)
        text = str(state.data)
    else:
        text = engine.run_tool('check_app', {'path': str(page)})
    assert 'OUTSIDE_SCOPE_FIXTURE' not in text
    assert '404' in text or 'not found' in text.lower()
