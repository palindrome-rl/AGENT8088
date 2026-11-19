"""A run that saves downloaded docs pages next to the page it builds was told "NOT VERIFIED: the page/script check failed
(pikalytics.html, stratadex.html, ollama_systemone.html)": the smoke test judged saved reference material as if it were the
deliverable. Pages in reference folders are skipped when the run also wrote a page elsewhere."""
from agent8088 import app_check


def _write(tmp_path, *names):
    out = []
    for name in names:
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("<html></html>", encoding="utf-8")
        out.append(str(p))
    return out


def test_saved_reference_pages_are_not_checked_when_a_real_page_exists(tmp_path):
    written = _write(tmp_path, "data/pikalytics.html", "data/ollama_systemone.html", "app/index.html", "research/stratadex.html")
    targets = app_check.deliverable_targets(written, [tmp_path], reference_pages=[written[0], written[1], written[3]])
    assert [t.name for t in targets] == ["index.html"]


def test_reference_pages_are_kept_when_nothing_else_was_written(tmp_path):
    written = _write(tmp_path, "data/pikalytics.html", "docs/guide.html")
    targets = app_check.deliverable_targets(written, [tmp_path], reference_pages=written)
    assert {t.name for t in targets} == {"pikalytics.html", "guide.html"}


def test_a_page_in_the_project_root_is_never_a_reference_page(tmp_path):
    assert not app_check.is_reference_page(tmp_path / "index.html", [tmp_path])
    assert app_check.is_reference_page(tmp_path / "Data" / "x.html", [tmp_path])
    assert not app_check.is_reference_page(tmp_path / "data" / "x.js", [tmp_path])


def test_a_project_that_lives_under_a_temp_or_data_folder_is_not_skipped(tmp_path):
    root = tmp_path / "Temp" / "data" / "myapp"
    written = _write(root, "index.html", "pages/about.html")
    assert {t.name for t in app_check.deliverable_targets(written, [root])} == {"index.html", "about.html"}


def test_scripts_and_the_limit_still_work(tmp_path):
    written = _write(tmp_path, "data/ref.html", "app/index.html", "app/server.js", "tool.py")
    names = [t.name for t in app_check.deliverable_targets(written, [tmp_path], reference_pages=[written[0]])]
    assert names[0] == "index.html" and "ref.html" not in names and len(names) <= 3


def test_authored_docs_and_data_pages_are_not_silently_skipped(tmp_path):
    written = _write(tmp_path, "index.html", "docs/guide.html", "data/results.html")
    assert {p.name for p in app_check.deliverable_targets(written, [tmp_path])} == {"index.html", "guide.html", "results.html"}


def test_download_provenance_is_cleared_when_the_page_is_rewritten():
    from agent8088.trajectory import TrajectoryState
    state = TrajectoryState({}, goal="Create a website", message_count=1)
    for reference in (True, False):
        state.after_tool(state.before_tool("write_file"), "Wrote page", failed=False, blocked=False,
                         mutated=True, path="docs/guide.html", reference=reference)
        assert bool(state.data["reference_paths"]) is reference
