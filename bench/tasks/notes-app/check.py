"""Grade a finished run of task 2A (notes app with a Python server).

usage: python check.py <workspace folder>     (e.g. runs/<run>/workspace; the notes/ folder is found inside it)
Starts the run's own server, drives the API, restarts it to check persistence, runs the run's own tests, and loads the page
from the real server in a browser. Prints one JSON object.
"""
import http.client, json, os, re, socket, subprocess, sys, tempfile, time, urllib.error, urllib.request
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # bench/, for graderlib
import graderlib  # noqa: E402


def find_notes(ws: Path):
    found = [p.parent for p in ws.rglob("server.py") if p.parent.name == "notes" and "node_modules" not in p.parts]
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
            raw = r.read()
            return r.status, raw
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except (OSError, http.client.HTTPException):
        # A server that drops the connection fails the step; it must not crash the checker.
        return 0, b""


def jcall(port, method, path, body=None):
    status, raw = call(port, method, path, body)
    try:
        return status, json.loads(raw.decode("utf-8") or "null")
    except ValueError:
        return status, None


class Server:
    def __init__(self, folder: Path, port: int, db_dir: Path | None = None):
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
    s, a = jcall(port, "POST", "/api/notes", {"title": "Alpha", "body": "first note about Cats", "tags": ["pets", "x"]})
    check("create returns 201 with id", s == 201 and isinstance(a, dict) and "id" in a, (s, a))
    s2, b = jcall(port, "POST", "/api/notes", {"title": "Beta", "body": "second", "tags": ["x"]})
    s3, c = jcall(port, "POST", "/api/notes", {"title": "Gamma", "body": "third about cats too", "tags": []})
    check("create two more", s2 == 201 and s3 == 201)
    s, bad = jcall(port, "POST", "/api/notes", {"body": "no title", "tags": []})
    check("missing title is 400 with error", s == 400 and isinstance(bad, dict) and "error" in bad, (s, bad))
    s, bad = jcall(port, "POST", "/api/notes", {"title": "", "body": "", "tags": []})
    check("empty title is 400", s == 400, s)
    s, lst = jcall(port, "GET", "/api/notes")
    check("list in id order", s == 200 and isinstance(lst, list) and [n["title"] for n in lst] == ["Alpha", "Beta", "Gamma"], lst)
    s, lst = jcall(port, "GET", "/api/notes?tag=x")
    check("filter by tag", s == 200 and [n["title"] for n in lst] == ["Alpha", "Beta"], lst)
    s, lst = jcall(port, "GET", "/api/notes?q=CATS")
    check("search ignores case, title or body", s == 200 and [n["title"] for n in lst] == ["Alpha", "Gamma"], lst)
    s, lst = jcall(port, "GET", "/api/notes?tag=x&q=cats")
    check("tag and text together", s == 200 and [n["title"] for n in lst] == ["Alpha"], lst)
    aid = a["id"] if isinstance(a, dict) and "id" in a else 1
    s, one = jcall(port, "GET", f"/api/notes/{aid}")
    check("get one", s == 200 and one.get("title") == "Alpha" and one.get("tags") == ["pets", "x"], one)
    s, miss = jcall(port, "GET", "/api/notes/99999")
    check("unknown id is 404 with error", s == 404 and isinstance(miss, dict) and "error" in miss, (s, miss))
    s, upd = jcall(port, "PUT", f"/api/notes/{aid}", {"title": "Alpha 2", "body": "edited", "tags": ["z"]})
    check("update returns the note", s == 200 and upd.get("title") == "Alpha 2" and upd.get("tags") == ["z"], (s, upd))
    s, _ = jcall(port, "PUT", "/api/notes/99999", {"title": "t", "body": "", "tags": []})
    check("update unknown id is 404", s == 404, s)
    s, _ = jcall(port, "PUT", f"/api/notes/{aid}", {"title": "", "body": "", "tags": []})
    check("update with empty title is 400", s == 400, s)
    bid = b["id"] if isinstance(b, dict) and "id" in b else 2
    s, raw = call(port, "DELETE", f"/api/notes/{bid}")
    check("delete is 204 with no body", s == 204 and not raw, (s, raw[:40]))
    s, _ = call(port, "DELETE", f"/api/notes/{bid}")
    check("delete again is 404", s == 404, s)
    s, lst = jcall(port, "GET", "/api/notes")
    check("list after edit and delete", s == 200 and [n["title"] for n in lst] == ["Alpha 2", "Gamma"], lst)
    return steps


def page_check(port) -> dict:
    out = {"served": False}
    s, raw = call(port, "GET", "/")
    html = raw.decode("utf-8", "replace")
    out["served"] = s == 200 and "<html" in html.lower()
    out["mentions_api"] = "/api/notes" in html or any(
        "/api/notes" in call(port, "GET", f"/{m}")[1].decode("utf-8", "replace")
        for m in re.findall(r'(?:src|href)="([^"]+\.js)"', html) if not m.startswith("http"))
    try:
        from playwright.sync_api import sync_playwright
        errors, failed = [], []
        with sync_playwright() as p:
            b = graderlib.launch_chromium(p)
            pg = b.new_page()
            pg.on("pageerror", lambda e: errors.append(str(e)[:120]))
            pg.on("response", lambda r: failed.append(f"{r.url[-40:]} {r.status}") if r.status >= 400 else None)
            pg.goto(f"http://127.0.0.1:{port}/", wait_until="load")
            pg.wait_for_timeout(600)
            out["visible_text_chars"] = len(pg.inner_text("body"))
            out["buttons"] = pg.locator("button:visible").count()
            b.close()
        out["page_errors"], out["failed_requests"] = errors[:4], failed[:4]
    except Exception as e:  # noqa: BLE001
        out["browser"] = f"unavailable: {str(e)[:100]}"
    return out


def main():
    ws = Path(sys.argv[1]).resolve()
    out = {"workspace": str(ws)}
    folders = find_notes(ws)
    out["notes_folders"] = [str(f.relative_to(ws)) for f in folders]
    if not folders:
        out["error"] = "no notes/server.py found"
        print(json.dumps(out, indent=1)); return
    folder = folders[0]
    out["files"] = sorted(str(p.relative_to(folder)).replace("\\", "/") for p in folder.rglob("*")
                          if p.is_file() and "__pycache__" not in p.parts and not p.name.endswith(".db"))[:40]
    out["has_readme"] = (folder / "README.md").exists()
    db = folder / "notes.db"
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
        s, lst = jcall(port, "GET", "/api/notes")
        out["persisted_after_restart"] = s == 200 and isinstance(lst, list) and [n.get("title") for n in lst] == ["Alpha 2", "Gamma"]
        srv2.stop()
    else:
        out["persisted_after_restart"] = False
    out["db_file_next_to_server"] = (folder / "notes.db").exists()
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
