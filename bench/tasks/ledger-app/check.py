"""Grade a finished run of task 2B (expense tracker with a Python server).

usage: python check.py <workspace folder>     (e.g. runs/<run>/workspace; the ledger/ folder is found inside it)
Starts the run's own server, drives the API (cents, validation, ranges, summary, CSV), restarts it to check persistence,
runs the run's own tests, and loads the page from the real server in a browser. Prints one JSON object.
"""
import csv, http.client, io, json, os, re, socket, subprocess, sys, time, urllib.error, urllib.request
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # bench/, for graderlib
import graderlib  # noqa: E402


def find_ledger(ws: Path):
    found = [p.parent for p in ws.rglob("server.py") if p.parent.name == "ledger" and "node_modules" not in p.parts]
    found.sort(key=lambda p: (len(p.parts), str(p)))
    return found


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def call(port, method, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read(), r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers.get("Content-Type", "")
    except (OSError, http.client.HTTPException):
        # A server that drops the connection fails the step; it must not crash the checker.
        return 0, b"", ""


def jcall(port, method, path, body=None):
    status, raw, _ = call(port, method, path, body)
    try:
        return status, json.loads(raw.decode("utf-8") or "null")
    except ValueError:
        return status, None


class Server:
    def __init__(self, folder: Path, port: int):
        self.folder, self.port, self.proc = folder, port, None

    def start(self):
        env = {**os.environ, "PORT": str(self.port), "PYTHONIOENCODING": "utf-8"}
        self.proc = subprocess.Popen([sys.executable, "server.py"], cwd=self.folder, env=env,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
        for _ in range(60):
            if self.proc.poll() is not None:
                return False
            try:
                socket.create_connection(("127.0.0.1", self.port), timeout=0.5).close()
                return True
            except OSError:
                time.sleep(0.25)
        return False

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(timeout=10)

    def output(self):
        try:
            self.stop()
            return (self.proc.stdout.read() if self.proc and self.proc.stdout else "")[-400:]
        except Exception:  # noqa: BLE001
            return ""


def api_flow(port) -> dict:
    steps = {}

    def check(name, ok, detail=""):
        steps[name] = {"ok": bool(ok), "detail": str(detail)[:160]}

    def add(date, amount, category, note=None):
        body = {"date": date, "amount": amount, "category": category}
        if note is not None:
            body["note"] = note
        return jcall(port, "POST", "/api/expenses", body)

    s, a = add("2026-03-05", "12.50", "Food", "lunch")
    check("create returns 201 with cents and lower-case category",
          s == 201 and isinstance(a, dict) and a.get("amount_cents") == 1250 and a.get("category") == "food"
          and a.get("note") == "lunch" and "id" in a, (s, a))
    s, b = add("2026-03-31", 0.1, "food")
    s2, c = add("2026-03-31", "0.2", "fun", 'said "hi", then\nleft')
    s3, d = add("2026-04-01", 100, "rent")
    s4, e = add("2026-02-28", "7.05", " Food ")
    check("floats and strings both give exact cents",
          s == 201 and b.get("amount_cents") == 10 and s2 == 201 and c.get("amount_cents") == 20
          and s3 == 201 and d.get("amount_cents") == 10000 and s4 == 201 and e.get("amount_cents") == 705
          and e.get("category") == "food" and b.get("note") == "", (b, c, d, e))
    for name, args in [("missing date", {"amount": "1", "category": "x"}),
                       ("impossible date", {"date": "2026-02-30", "amount": "1", "category": "x"}),
                       ("bad date shape", {"date": "03/05/2026", "amount": "1", "category": "x"}),
                       ("zero amount", {"date": "2026-03-05", "amount": "0", "category": "x"}),
                       ("negative amount", {"date": "2026-03-05", "amount": "-4", "category": "x"}),
                       ("three decimals", {"date": "2026-03-05", "amount": "1.005", "category": "x"}),
                       ("text amount", {"date": "2026-03-05", "amount": "abc", "category": "x"}),
                       ("blank category", {"date": "2026-03-05", "amount": "1", "category": "   "})]:
        s, r = jcall(port, "POST", "/api/expenses", args)
        check(f"400 for {name}", s == 400 and isinstance(r, dict) and "error" in r, (s, r))
    s, lst = jcall(port, "GET", "/api/expenses")
    order = [(x["date"], x["id"]) for x in lst] if isinstance(lst, list) else []
    check("list ordered by date then id", s == 200 and len(order) == 5 and order == sorted(order), order)
    s, lst = jcall(port, "GET", "/api/expenses?from=2026-03-05&to=2026-03-31")
    check("date range is inclusive at both ends", s == 200 and [x["amount_cents"] for x in lst] == [1250, 10, 20], lst)
    s, lst = jcall(port, "GET", "/api/expenses?category=FOOD&from=2026-03-01")
    check("category filter ignores case and combines", s == 200 and [x["amount_cents"] for x in lst] == [1250, 10], lst)
    s, r = jcall(port, "GET", "/api/expenses?from=yesterday")
    check("malformed date filter is 400", s == 400, (s, r))
    s, r = jcall(port, "GET", "/api/summary?month=2026-03")
    check("summary for a month", s == 200 and r.get("count") == 3 and r.get("total_cents") == 1280
          and r.get("by_category") == {"food": 1260, "fun": 20} and r.get("month") == "2026-03", r)
    s, r = jcall(port, "GET", "/api/summary?month=2025-01")
    check("empty month", s == 200 and r.get("count") == 0 and r.get("total_cents") == 0 and r.get("by_category") == {}, r)
    s, r = jcall(port, "GET", "/api/summary?month=2026-13")
    check("bad month is 400", s == 400, (s, r))
    s, raw, ctype = call(port, "GET", "/api/export.csv")
    rows = list(csv.reader(io.StringIO(raw.decode("utf-8", "replace"), newline=""))) if s == 200 else []
    check("csv has text/csv type and header", s == 200 and "text/csv" in ctype and rows[:1] == [["id", "date", "amount", "category", "note"]], (ctype, rows[:1]))
    check("csv rows match list, amounts in dollars", len(rows) == 6 and [r[2] for r in rows[1:]] == ["7.05", "12.50", "0.10", "0.20", "100.00"], rows)
    check("csv returns notes with quotes, commas and line breaks intact", any(r[4] == 'said "hi", then\nleft' for r in rows[1:]), rows[-3:])
    cid = a["id"] if isinstance(a, dict) and "id" in a else 1
    s, raw, _ = call(port, "DELETE", f"/api/expenses/{cid}")
    check("delete is 204 with no body", s == 204 and not raw, (s, raw[:40]))
    s, r = jcall(port, "DELETE", f"/api/expenses/{cid}")
    check("delete again is 404 with error", s == 404 and isinstance(r, dict) and "error" in r, (s, r))
    s, r = jcall(port, "GET", "/api/summary?month=2026-03")
    check("summary after delete", s == 200 and r.get("total_cents") == 30 and r.get("count") == 2, r)
    return steps


def page_check(port) -> dict:
    out = {"served": False}
    s, raw, _ = call(port, "GET", "/")
    html = raw.decode("utf-8", "replace")
    out["served"] = s == 200 and "<html" in html.lower()
    try:
        from playwright.sync_api import sync_playwright
        errors, failed = [], []
        with sync_playwright() as p:
            b = graderlib.launch_chromium(p)
            pg = b.new_page()
            pg.on("pageerror", lambda e: errors.append(str(e)[:120]))
            pg.on("response", lambda r: failed.append(f"{r.url[-40:]} {r.status}") if r.status >= 400 else None)
            pg.goto(f"http://127.0.0.1:{port}/", wait_until="load")
            pg.wait_for_timeout(800)
            text = pg.inner_text("body")
            out["visible_text_chars"] = len(text)
            out["shows_dollars"] = bool(re.search(r"\$\s?\d+\.\d\d", text))
            out["buttons"] = pg.locator("button:visible").count()
            out["inputs"] = pg.locator("input:visible, select:visible").count()
            out["delete_buttons"] = pg.locator("button:visible", has_text=re.compile("delete|remove|×|✕|x", re.I)).count()
            b.close()
        out["page_errors"], out["failed_requests"] = errors[:4], failed[:4]
    except Exception as e:  # noqa: BLE001
        out["browser"] = f"unavailable: {str(e)[:100]}"
    return out


def main():
    ws = Path(sys.argv[1]).resolve()
    out = {"workspace": str(ws)}
    folders = find_ledger(ws)
    out["ledger_folders"] = [str(f.relative_to(ws)) for f in folders]
    if not folders:
        out["error"] = "no ledger/server.py found"
        print(json.dumps(out, indent=1)); return
    folder = folders[0]
    out["files"] = sorted(str(p.relative_to(folder)).replace("\\", "/") for p in folder.rglob("*")
                          if p.is_file() and "__pycache__" not in p.parts and not p.name.endswith(".db"))[:40]
    out["has_readme"] = (folder / "README.md").exists()
    db = folder / "ledger.db"
    if db.exists():
        db.unlink()
    port = free_port()
    srv = Server(folder, port)
    if not srv.start():
        out["server_started"] = False
        out["server_output"] = srv.output()
        print(json.dumps(out, indent=1)); return
    out["server_started"] = True
    try:
        steps = api_flow(port)
        out["api"] = steps
        out["api_passed"] = sum(v["ok"] for v in steps.values())
        out["api_total"] = len(steps)
        out["page"] = page_check(port)
    finally:
        srv.stop()
    srv2 = Server(folder, port)
    if srv2.start():
        s, lst = jcall(port, "GET", "/api/expenses")
        out["persisted_after_restart"] = s == 200 and isinstance(lst, list) and [x.get("amount_cents") for x in lst] == [705, 10, 20, 10000]
        srv2.stop()
    else:
        out["persisted_after_restart"] = False
    out["db_file_next_to_server"] = (folder / "ledger.db").exists()
    tests = folder / "tests"
    if tests.exists():
        r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"], cwd=folder,
                           capture_output=True, text=True, timeout=180, errors="replace")
        text = r.stdout + r.stderr
        out["own_tests"] = {"returncode": r.returncode,
                            "ran": sum(int(x) for x in re.findall(r"Ran (\d+) test", text)), "tail": text[-200:]}
    else:
        out["own_tests"] = {"missing": True}
    out["all_pass"] = bool(out.get("api_passed") == out.get("api_total") and out.get("persisted_after_restart")
                           and out.get("page", {}).get("served") and out["own_tests"].get("returncode") == 0)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
