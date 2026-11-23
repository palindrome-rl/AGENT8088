"""Scripted checker for the Team Board task.

usage: python check.py <folder containing the finished app (searched for index.html)> [--json out.json]
Plays 8 scenarios in a headless browser against the page served from a local static server and
prints a JSON report. The first 7 scenarios and a console-error check are worth 60 points together.
"""
import functools, http.server, json, os, sys, tempfile, threading, time
from pathlib import Path
from playwright.sync_api import sync_playwright
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # bench/, for graderlib
import graderlib  # noqa: E402

COLS = ["To Do", "In Progress", "In Review", "Done"]
SEED_COLS = {"C1": "Done", "C2": "In Progress", "C3": "In Progress", "C4": "In Progress", "C5": "In Progress",
             "C6": "In Review", "C7": "In Review", "C8": "In Review", "C9": "To Do", "C10": "To Do"}


def find_index(folder):
    hits = sorted(Path(folder).rglob("index.html"), key=lambda p: (len(p.parts), str(p)))
    hits = [h for h in hits if "node_modules" not in h.parts]
    return hits[0] if hits else None


class Server:
    def __init__(self, root):
        class Quiet(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *a):
                pass
        handler = functools.partial(Quiet, directory=str(root))
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown(); self.httpd.server_close()


def run(folder):
    index = find_index(folder)
    if not index:
        return {"error": "no index.html found", "score": 0, "scenarios": []}
    srv = Server(index.parent)
    url = f"http://127.0.0.1:{srv.port}/index.html"
    results, errors = [], []
    with sync_playwright() as p:
        browser = graderlib.launch_chromium(p)

        def fresh():
            ctx = browser.new_context(accept_downloads=True)
            page = ctx.new_page()
            page.set_default_timeout(3000)
            page.on("pageerror", lambda e: errors.append(str(e)[:160]))
            page.on("dialog", lambda d: d.accept())
            page.goto(url)
            page.wait_for_timeout(500)
            return ctx, page

        def col_of(page, cid):
            return page.evaluate("""(id) => { const c = document.querySelector('[data-card-id="' + id + '"]');
                const k = c && c.closest('[data-column]'); return k ? k.dataset.column : null; }""", cid)

        def msg(page):
            el = page.query_selector('[data-role="message"]')
            return (el.inner_text() if el else "").strip()

        def move(page, cid, col):
            page.select_option(f'[data-card-id="{cid}"] select[data-role="move"]', col)
            page.wait_for_timeout(250)

        def board(page):
            return {cid: col_of(page, cid) for cid in SEED_COLS}

        def scenario(name, fn):
            ctx = None
            try:
                ctx, page = fresh()
                ok, note = fn(page, ctx)
            except Exception as e:  # noqa: BLE001
                ok, note = False, f"error: {str(e).splitlines()[0][:160]}"
            finally:
                if ctx:
                    ctx.close()
            results.append({"name": name, "ok": bool(ok), "note": note})

        def s_load(page, ctx):
            got = board(page)
            bad = {k: (got[k], v) for k, v in SEED_COLS.items() if got[k] != v}
            return not bad, "all 10 seed cards in the right columns" if not bad else f"wrong: {bad}"

        def s_crud(page, ctx):
            page.click('[data-role="add-card"]')
            page.fill('[name="title"]', "Checker card")
            for field, val in (("assignee", "Dina"), ("priority", "High")):
                try:
                    page.select_option(f'[name="{field}"]', val)
                except Exception:  # noqa: BLE001
                    page.fill(f'[name="{field}"]', val)
            page.fill('[name="due"]', "2026-12-01")
            page.fill('[name="checklist"]', "one\ntwo")
            page.click('[data-role="save-card"]')
            page.wait_for_timeout(300)
            new = page.query_selector('[data-column="To Do"] [data-card-id]:has-text("Checker card")')
            if not new:
                return False, "added card not found in To Do"
            cid = new.get_attribute("data-card-id")
            if len(page.query_selector_all(f'[data-card-id="{cid}"] input[data-role="check"]')) != 2:
                return False, "new card does not have 2 checklist items"
            page.click(f'[data-card-id="{cid}"] [data-role="edit-card"]')
            page.fill('[name="title"]', "Checker card edited")
            page.click('[data-role="save-card"]')
            page.wait_for_timeout(300)
            if not page.query_selector(f'[data-card-id="{cid}"]:has-text("Checker card edited")'):
                return False, "edit did not change the title"
            page.click(f'[data-card-id="{cid}"] [data-role="delete-card"]')
            page.wait_for_timeout(300)
            if page.query_selector(f'[data-card-id="{cid}"]'):
                return False, "delete did not remove the card"
            return True, "add, edit and delete work"

        def s_limit(page, ctx):
            move(page, "C9", "In Progress")
            if col_of(page, "C9") != "To Do":
                return False, "Ben's 4th In Progress card was not refused"
            m = msg(page)
            if "Ben" not in m:
                return False, f"refusal message does not name Ben: {m!r}"
            move(page, "C10", "In Progress")
            if col_of(page, "C10") != "In Progress":
                return False, "Chen (1 card in progress) was refused"
            return True, "limit enforced per person, message names Ben"

        def s_done(page, ctx):
            move(page, "C7", "Done")
            if col_of(page, "C7") != "In Review":
                return False, "card with unfinished checklist moved to Done"
            if not msg(page):
                return False, "no message for the refused move to Done"
            boxes = page.locator('[data-card-id="C7"] input[data-role="check"]')
            for i in range(boxes.count()):
                box = page.locator('[data-card-id="C7"] input[data-role="check"]').nth(i)
                if not box.is_checked():
                    box.check()
                page.wait_for_timeout(150)
            move(page, "C7", "Done")
            if col_of(page, "C7") != "Done":
                return False, "card with finished checklist could not move to Done"
            move(page, "C6", "Done")
            return col_of(page, "C6") == "Done", "Done gate works" if col_of(page, "C6") == "Done" else "C6 (all ticked) refused"

        def badge(page, cid):
            return page.query_selector(f'[data-card-id="{cid}"] [data-role="badge"]:has-text("Overdue review")') is not None

        def s_overdue(page, ctx):
            page.click('[data-role="advance-day"]'); page.wait_for_timeout(250)
            if not badge(page, "C6"):
                return False, "C6 (1 day + 1) has no Overdue review label after one day"
            if badge(page, "C7") or badge(page, "C8"):
                return False, "C7/C8 labelled too early"
            first = page.evaluate("""() => { const k = document.querySelector('[data-column="In Review"]');
                const c = k.querySelector('[data-card-id]'); return c ? c.dataset.cardId : null; }""")
            if first != "C6":
                return False, f"overdue card not listed first (first is {first})"
            page.click('[data-role="advance-day"]'); page.wait_for_timeout(250)
            if not (badge(page, "C7") and badge(page, "C8")):
                return False, "C7/C8 not labelled after two days"
            move(page, "C6", "Done"); move(page, "C6", "In Review")
            if badge(page, "C6"):
                return False, "moving a card did not reset its days"
            return True, "overdue label, ordering and reset work"

        def s_data(page, ctx):
            move(page, "C10", "In Progress")
            page.reload(); page.wait_for_timeout(500)
            if col_of(page, "C10") != "In Progress":
                return False, "move lost after reload"
            with page.expect_download() as d:
                page.click('[data-role="export"]')
            path = d.value.path()
            data = json.load(open(path, encoding="utf-8"))
            if data.get("schema") != "teamboard-1" or len(data.get("cards", [])) != 10:
                return False, f"export format wrong: schema={data.get('schema')} cards={len(data.get('cards', []))}"
            tmp = Path(tempfile.mkdtemp())
            good = json.loads(json.dumps(data)); good["cards"][0]["column"] = "To Do"
            gp = tmp / "good.json"; gp.write_text(json.dumps(good), encoding="utf-8")
            page.set_input_files('input[data-role="import"]', str(gp)); page.wait_for_timeout(500)
            first = good["cards"][0]["id"]
            if col_of(page, first) != "To Do":
                return False, "valid import was not applied"
            before = board(page)
            for name, content in (("bad_col.json", json.dumps({**good, "cards": [{**good["cards"][1], "column": "Archive"}] + good["cards"][2:]})),
                                  ("bad_schema.json", json.dumps({**good, "schema": "other"})),
                                  ("not_json.json", "{{ nope")):
                bp = tmp / name; bp.write_text(content, encoding="utf-8")
                page.set_input_files('input[data-role="import"]', str(bp)); page.wait_for_timeout(500)
                if board(page) != before:
                    return False, f"broken import ({name}) changed the board"
            if not msg(page):
                return False, "no message shown for a refused import"
            return True, "persistence, export format, valid import and atomic refusal work"

        def s_undo(page, ctx):
            move(page, "C10", "In Progress"); move(page, "C5", "In Review"); move(page, "C3", "In Review")
            for _ in range(3):
                page.click('[data-role="undo"]'); page.wait_for_timeout(200)
            if board(page) != SEED_COLS:
                return False, f"three undos did not restore the seed board: {board(page)}"
            page.click('[data-role="advance-day"]'); page.wait_for_timeout(200)
            page.click('[data-role="undo"]'); page.wait_for_timeout(200)
            if badge(page, "C6"):
                return False, "undo of advance-day left the label"
            return True, "multi-step undo and undo of advance day work"

        for name, fn in (("1 seed board loads", s_load), ("2 add, edit, delete card", s_crud),
                         ("3 per-person In Progress limit", s_limit), ("4 Done needs a finished checklist", s_done),
                         ("5 overdue review label, order, reset", s_overdue),
                         ("6 persistence, export, import, atomic refusal", s_data), ("7 undo", s_undo)):
            scenario(name, fn)
        browser.close()
    srv.close()
    results.append({"name": "8 no uncaught page errors", "ok": not errors, "note": "none" if not errors else "; ".join(sorted(set(errors))[:3])})
    passed = sum(r["ok"] for r in results)
    return {"index": str(index), "scenarios": results, "passed": passed, "of": len(results),
            "score_60": round(60 * passed / len(results), 1)}


if __name__ == "__main__":
    out = run(sys.argv[1])
    if "--json" in sys.argv:
        Path(sys.argv[sys.argv.index("--json") + 1]).write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2))
