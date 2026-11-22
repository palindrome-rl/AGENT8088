"""Grader for catalog-scrape.

    python grade.py <workspace> --json <report.json>

Serves two shops on free local ports, the copy the task gave (fixture/site, not
the workspace's copy, which the run may have changed) and a second one with
other products, more pages and next links in another style, and runs the
workspace's scrape.py against each from an empty folder. The rows must match
exactly. A script that only works for the given copy fails the second shop.
"""
import argparse
import ast
import csv
import functools
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))  # bench/, for graderlib
sys.path.insert(0, str(HERE))
import graderlib
import shop

HEADER = ["sku", "name", "price", "in_stock"]
TOTAL = 6


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class Served:
    """A folder served over HTTP on a free local port, for as long as the block runs."""

    def __init__(self, folder: Path):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_Quiet, directory=str(folder)))
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/"

    def __enter__(self):
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


def run_scraper(script: Path, url: str):
    """(exit status, rows or None, what went wrong) for one run from an empty folder."""
    with tempfile.TemporaryDirectory() as folder:
        try:
            done = subprocess.run([sys.executable, str(script), url], cwd=folder, capture_output=True, text=True,
                                  timeout=120, encoding="utf-8", errors="replace", check=False)
        except subprocess.TimeoutExpired:
            return None, None, "timed out after 120 s"
        out = Path(folder) / "products.csv"
        if not out.is_file():
            return done.returncode, None, (done.stderr.strip().splitlines() or ["no products.csv written"])[-1][:200]
        with out.open(newline="", encoding="utf-8-sig") as handle:
            return done.returncode, list(csv.reader(handle)), ""


def non_stdlib_imports(script: Path) -> list:
    try:
        tree = ast.parse(script.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return ["<does not parse>"]
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.add(node.module.split(".")[0])
    return sorted(name for name in names if name not in sys.stdlib_module_names and name != "__future__")


def grade(workspace: Path) -> dict:
    script = workspace / "scrape.py"
    if not script.is_file():
        return graderlib.report({"scrape.py exists": False}, total=TOTAL)
    checks, notes = {}, {}
    with tempfile.TemporaryDirectory() as folder:
        second = Path(folder) / "second"
        shop.write_shop(second, shop.SECOND)
        given = Path(folder) / "given"
        shutil.copytree(HERE / "fixture" / "site", given)
        with Served(given) as served:
            status, rows, why = run_scraper(script, served.url)
        checks["runs on the given shop"] = status == 0 and rows is not None
        checks["the header is sku,name,price,in_stock"] = bool(rows) and rows[0] == HEADER
        checks["every row is right on the given shop"] = bool(rows) and rows[1:] == shop.expected_rows(shop.FIXTURE)
        if why:
            notes["given shop"] = why
        with Served(second) as served:
            status, rows2, why = run_scraper(script, served.url)
        checks["runs on a second shop with more pages"] = status == 0 and rows2 is not None
        checks["every row is right on the second shop"] = bool(rows2) and rows2[1:] == shop.expected_rows(shop.SECOND)
        if why:
            notes["second shop"] = why
    foreign = non_stdlib_imports(script)
    checks["uses only the standard library"] = not foreign
    extra = {"notes": notes} if notes else {}
    if foreign:
        extra["imports"] = foreign
    if rows and rows[1:] != shop.expected_rows(shop.FIXTURE):
        extra["given_rows_seen"] = rows[1:4]
    return graderlib.report(checks, total=TOTAL, **extra)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("workspace")
    parser.add_argument("--json", required=True)
    args = parser.parse_args()
    graderlib.write(grade(Path(args.workspace)), args.json)


if __name__ == "__main__":
    sys.exit(main())
