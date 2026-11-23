"""The sandbox task graders pass reference solutions and fail broken ones.

Each broken solution is one a run could plausibly leave behind and still
describe as done: the call deleted instead of fixed, a dependency left out of
the bundle, notes kept only in memory, a chart that loads a script from the
internet. Tests that need Node or a browser are skipped where there is none.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

TASKS = Path(__file__).resolve().parent.parent / "bench" / "tasks"


def _grade(task: str, workspace: Path, tmp_path: Path, env=None) -> dict:
    out = tmp_path / f"grade-{task}.json"
    subprocess.run([sys.executable, str(TASKS / task / "grade.py"), str(workspace), "--json", str(out)],
                   check=True, capture_output=True, timeout=600, env=env)
    return json.loads(out.read_text(encoding="utf-8"))


def _fixture(task: str, tmp_path: Path) -> Path:
    workspace = tmp_path / task
    source = TASKS / task / "fixture"
    if source.is_dir():
        shutil.copytree(source, workspace)
    else:
        workspace.mkdir()
    return workspace


def _all_tasks_have_a_grader_test():
    return {"pip-venv-report", "slow-build-fix", "npm-esbuild-bundle", "http-api-server", "render-report-page"}


# Long tasks are covered in test_bench_long_graders.py.
LONG_TASKS = {"kanban-board", "qbr-review-pack", "notes-app", "ledger-app"}
# The game, page and scraper tasks are covered in test_bench_web_tasks.py.
WEB_TASKS = {"tile-flip-game", "three-in-row-game", "cart-page", "catalog-scrape"}
# Tasks whose graders need the task's services running; tested in test_bench_service_tasks.py.
SERVICE_TASKS = {"price-watch", "expense-erp"}


def test_every_task_has_grader_tests():
    shipped = {folder.name for folder in TASKS.iterdir() if (folder / "task.json").is_file()}
    covered = _all_tasks_have_a_grader_test() | LONG_TASKS | WEB_TASKS | SERVICE_TASKS
    assert shipped == covered, shipped ^ covered


@pytest.mark.parametrize("task", sorted(_all_tasks_have_a_grader_test()))
def test_an_empty_workspace_fails_or_is_ungraded(task, tmp_path):
    workspace = tmp_path / "empty"
    workspace.mkdir()
    report = _grade(task, workspace, tmp_path)
    assert report.get("passed") is not True


# ----------------------------------------------------------------- slow-build-fix

def _build(workspace: Path):
    subprocess.run([sys.executable, "build.py"], cwd=workspace, check=True, capture_output=True,
                   env={**os.environ, "BUILD_FAST": "1"}, timeout=120)


def _fix_typo(workspace: Path, new="use parse_header"):
    module = workspace / "src" / "modules" / "module_077.txt"
    module.write_text(module.read_text(encoding="utf-8").replace("use parse_heder", new), encoding="utf-8")


def test_slow_build_passes_the_real_fix(tmp_path):
    workspace = _fixture("slow-build-fix", tmp_path)
    _fix_typo(workspace)
    _build(workspace)
    report = _grade("slow-build-fix", workspace, tmp_path)
    assert report["passed"], report["checks"]


def test_slow_build_fails_when_the_call_is_deleted(tmp_path):
    workspace = _fixture("slow-build-fix", tmp_path)
    _fix_typo(workspace, new="")
    _build(workspace)
    report = _grade("slow-build-fix", workspace, tmp_path)
    assert not report["passed"]
    assert not report["checks"]["module_077 calls parse_header"]


def test_slow_build_fails_with_a_stand_in_symbol(tmp_path):
    workspace = _fixture("slow-build-fix", tmp_path)
    stub = workspace / "src" / "modules" / "module_999.txt"
    stub.write_text("def parse_heder\n", encoding="utf-8")
    _build(workspace)
    report = _grade("slow-build-fix", workspace, tmp_path)
    assert not report["passed"]
    assert not report["checks"]["no stand-in symbol was defined"]


def test_slow_build_fails_when_build_py_was_changed(tmp_path):
    workspace = _fixture("slow-build-fix", tmp_path)
    _fix_typo(workspace)
    build = workspace / "build.py"
    build.write_text(build.read_text(encoding="utf-8").replace("1.5", "0.0"), encoding="utf-8")
    _build(workspace)
    report = _grade("slow-build-fix", workspace, tmp_path)
    assert not report["checks"]["build.py unchanged"]


def test_slow_build_fails_without_a_successful_build_output(tmp_path):
    workspace = _fixture("slow-build-fix", tmp_path)
    _fix_typo(workspace)
    report = _grade("slow-build-fix", workspace, tmp_path)
    assert not report["passed"]
    assert not report["checks"]["out/ is from a successful build"]
    assert report["checks"]["the build gives the right program"]


# ----------------------------------------------------------------- npm-esbuild-bundle

needs_node = pytest.mark.skipif(shutil.which("node") is None or shutil.which("npm") is None, reason="needs node and npm")

# Stands in for esbuild so the grader can be tested offline: it writes a bundle
# with the program's real output baked in, or one that still needs node_modules.
FAKE_BUNDLER = """const fs = require("fs");
const lines = fs.readFileSync(process.argv[2], "utf8").trim().split(/\\r?\\n/);
fs.mkdirSync("dist", { recursive: true });
const body = lines.map(l => `console.log(${JSON.stringify(l)});`).join("\\n");
fs.writeFileSync("dist/app.js", (process.argv[3] === "external" ? 'require("dayjs");\\n' : "") + body + "\\n");
"""


def _npm_project(workspace: Path, external=False):
    shutil.copyfile(TASKS / "npm-esbuild-bundle" / "expected_output.txt", workspace / "expected.txt")
    (workspace / "fake-esbuild.js").write_text(FAKE_BUNDLER, encoding="utf-8")
    script = "node fake-esbuild.js expected.txt" + (" external" if external else "")
    (workspace / "package.json").write_text(json.dumps({
        "name": "orders", "version": "1.0.0", "scripts": {"build": script},
        "dependencies": {"dayjs": "^1.11.0"}, "devDependencies": {"esbuild": "^0.24.0"}}), encoding="utf-8")
    for name in ("esbuild", "dayjs"):
        folder = workspace / "node_modules" / name
        folder.mkdir(parents=True)
        (folder / "package.json").write_text(json.dumps({"name": name, "main": "index.js"}), encoding="utf-8")
        (folder / "index.js").write_text("module.exports = {};\n", encoding="utf-8")


@needs_node
def test_npm_bundle_passes_a_self_contained_bundle(tmp_path):
    workspace = _fixture("npm-esbuild-bundle", tmp_path)
    _npm_project(workspace)
    report = _grade("npm-esbuild-bundle", workspace, tmp_path)
    assert report["passed"], report["checks"]
    assert (workspace / "node_modules").is_dir(), "node_modules is put back after the check"


@needs_node
def test_npm_bundle_fails_a_bundle_that_still_needs_node_modules(tmp_path):
    workspace = _fixture("npm-esbuild-bundle", tmp_path)
    _npm_project(workspace, external=True)
    report = _grade("npm-esbuild-bundle", workspace, tmp_path)
    assert not report["passed"]
    assert not report["checks"]["dist/app.js runs without node_modules"]


@needs_node
def test_npm_bundle_fails_without_the_dev_dependency(tmp_path):
    workspace = _fixture("npm-esbuild-bundle", tmp_path)
    _npm_project(workspace)
    package = json.loads((workspace / "package.json").read_text(encoding="utf-8"))
    package["dependencies"]["esbuild"] = package.pop("devDependencies")["esbuild"]
    (workspace / "package.json").write_text(json.dumps(package), encoding="utf-8")
    report = _grade("npm-esbuild-bundle", workspace, tmp_path)
    assert not report["checks"]["esbuild is a dev dependency"]


# ----------------------------------------------------------------- http-api-server

SERVER = '''import json, os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

STORE = Path(__file__).with_name("notes.json")
PERSIST = True


def load():
    if PERSIST and STORE.exists():
        return json.loads(STORE.read_text())
    return {"next": 1, "notes": []}


STATE = load()


def save():
    if PERSIST:
        STORE.write_text(json.dumps(STATE))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, status, body=None):
        self.send_response(status)
        if body is not None:
            data = json.dumps(body).encode()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            self.end_headers()

    def note(self):
        try:
            wanted = int(self.path.rsplit("/", 1)[1])
        except ValueError:
            return None
        return next((n for n in STATE["notes"] if n["id"] == wanted), None)

    def do_GET(self):
        if self.path == "/health":
            return self.send(200, {"status": "ok"})
        if self.path == "/notes":
            return self.send(200, STATE["notes"])
        found = self.note() if self.path.startswith("/notes/") else None
        return self.send(200, found) if found else self.send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/notes":
            return self.send(404, {"error": "not found"})
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        try:
            text = json.loads(raw).get("text")
        except (ValueError, AttributeError):
            text = None
        if not isinstance(text, str) or not text:
            return self.send(400, {"error": "text is required"})
        note = {"id": STATE["next"], "text": text}
        STATE["next"] += 1
        STATE["notes"].append(note)
        save()
        return self.send(201, note)

    def do_DELETE(self):
        found = self.note() if self.path.startswith("/notes/") else None
        if not found:
            return self.send(404, {"error": "not found"})
        STATE["notes"].remove(found)
        save()
        return self.send(204)


ThreadingHTTPServer(("127.0.0.1", int(os.environ.get("PORT", 8765))), Handler).serve_forever()
'''


def test_http_api_passes_a_reference_server(tmp_path):
    workspace = _fixture("http-api-server", tmp_path)
    (workspace / "server.py").write_text(SERVER, encoding="utf-8")
    (workspace / "notes.json").write_text('{"next": 7, "notes": [{"id": 6, "text": "theirs"}]}', encoding="utf-8")
    report = _grade("http-api-server", workspace, tmp_path)
    assert report["passed"], report["checks"]
    # The agent's own notes are put back after the check.
    assert json.loads((workspace / "notes.json").read_text(encoding="utf-8"))["next"] == 7


def test_http_api_fails_a_server_that_forgets_on_restart(tmp_path):
    workspace = _fixture("http-api-server", tmp_path)
    (workspace / "server.py").write_text(SERVER.replace("PERSIST = True", "PERSIST = False"), encoding="utf-8")
    report = _grade("http-api-server", workspace, tmp_path)
    assert not report["passed"]
    assert not report["checks"]["notes persist across a restart"]


def test_http_api_fails_a_server_that_does_not_start(tmp_path):
    workspace = _fixture("http-api-server", tmp_path)
    (workspace / "server.py").write_text("raise SystemExit('broken')\n", encoding="utf-8")
    report = _grade("http-api-server", workspace, tmp_path)
    assert report["passed"] is False
    assert report["score"] == 0.0


# ----------------------------------------------------------------- render-report-page

def _browser_env():
    env = dict(os.environ)
    shared = Path(os.environ.get("LOCALAPPDATA", "")) / "agent8088" / "playwright-browsers" \
        if sys.platform == "win32" else Path.home() / ".agent8088" / "playwright-browsers"
    if "PLAYWRIGHT_BROWSERS_PATH" not in env and shared.is_dir():
        env["PLAYWRIGHT_BROWSERS_PATH"] = str(shared)
    return env


def _page(workspace: Path, scale=None, extra_head=""):
    data = json.loads((workspace / "data.json").read_text(encoding="utf-8"))
    top = max(m["value"] for m in data["months"])
    rows = "".join(f'<tr data-month="{m["month"]}"><td>{m["month"]}</td><td>{m["value"]}</td></tr>'
                   for m in data["months"])
    bars = "".join(
        f'<rect class="bar" data-month="{m["month"]}" x="{i * 30}" y="0" width="20" '
        f'height="{(scale or (lambda v: 200 * v / top))(m["value"]):.2f}"></rect>'
        for i, m in enumerate(data["months"]))
    (workspace / "report.html").write_text(
        f"<!doctype html><html><head><meta charset='utf-8'>{extra_head}</head><body><h1>{data['title']}</h1>"
        f"<table>{rows}</table><svg width='400' height='220'>{bars}</svg></body></html>", encoding="utf-8")


@pytest.fixture
def page_workspace(tmp_path):
    workspace = _fixture("render-report-page", tmp_path)
    probe = tmp_path / "probe"
    probe.mkdir()
    (probe / "report.html").write_text("<h1>x</h1>", encoding="utf-8")
    report = _grade("render-report-page", probe, tmp_path, env=_browser_env())
    if "error" in report:
        pytest.skip(report["error"])
    return workspace


def test_render_page_passes_a_reference_page(page_workspace, tmp_path):
    _page(page_workspace)
    report = _grade("render-report-page", page_workspace, tmp_path, env=_browser_env())
    assert report["passed"], report


def test_render_page_fails_bars_that_do_not_follow_the_values(page_workspace, tmp_path):
    _page(page_workspace, scale=lambda value: 100)
    report = _grade("render-report-page", page_workspace, tmp_path, env=_browser_env())
    assert not report["checks"]["bar heights follow the values"]


def test_render_page_fails_a_page_that_loads_from_the_internet(page_workspace, tmp_path):
    _page(page_workspace, extra_head='<script src="https://cdn.example.com/chart.js"></script>')
    report = _grade("render-report-page", page_workspace, tmp_path, env=_browser_env())
    assert not report["passed"]
    assert not report["checks"]["no requests leave the machine"]


def test_a_grader_that_cannot_start_a_browser_reports_ungraded(tmp_path):
    workspace = _fixture("render-report-page", tmp_path)
    _page(workspace)
    env = {**os.environ, "PLAYWRIGHT_BROWSERS_PATH": str(tmp_path / "no-browsers-here")}
    report = _grade("render-report-page", workspace, tmp_path, env=env)
    assert "passed" not in report
    assert "browser" in report["error"]
