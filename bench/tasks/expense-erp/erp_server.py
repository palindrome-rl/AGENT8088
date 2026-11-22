"""Mock expense ERP for the expense-report task: http://127.0.0.1:8910/

usage: python erp_server.py serve <state_dir>      (state in <state_dir>/erp.json)
Plain HTML forms, a login, a draft list, and an irreversible two-step "Submit to finance".
Login: see fixture/login.txt.
"""
import html, json, os, secrets, sys, threading, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

USER, PASSWORD = "clerk", "harbor-demo-1"
CATEGORIES = ["Meals", "Transport", "Lodging", "Office supplies", "Software"]
LOCK = threading.Lock()


class ERP(BaseHTTPRequestHandler):
    state_path = "erp.json"
    sessions = set()

    def log_message(self, *a):
        pass

    # ---- state
    def load(self):
        if os.path.exists(self.state_path):
            return json.load(open(self.state_path, encoding="utf-8"))
        return {"expenses": [], "next_id": 1}

    def save(self, st):
        json.dump(st, open(self.state_path, "w", encoding="utf-8"), indent=1)

    # ---- helpers
    def authed(self):
        c = self.headers.get("Cookie", "")
        return any(f"sid={s}" in c for s in self.sessions)

    def page(self, body, code=200, extra=None):
        nav = '<nav><a href="/expenses">Expenses</a> | <a href="/expenses/new">New expense</a> | <a href="/logout">Log out</a></nav><hr>'
        data = f"<!doctype html><html><head><meta charset='utf-8'><title>Harbor ERP</title></head><body><h1>Harbor ERP - Expenses</h1>{nav if self.authed() else ''}{body}</body></html>".encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(data)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers(); self.wfile.write(data)

    def redirect(self, to, extra=None):
        self.send_response(303); self.send_header("Location", to)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()

    def form(self):
        n = int(self.headers.get("Content-Length") or 0)
        return {k: v[0] for k, v in urllib.parse.parse_qs(self.rfile.read(n).decode("utf-8")).items()}

    # ---- routes
    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/login"):
            return self.page('<h2>Log in</h2><form method="post" action="/login"><label>User <input name="username"></label><br>'
                             '<label>Password <input name="password" type="password"></label><br><button type="submit">Log in</button></form>')
        if path == "/logout":
            return self.redirect("/login", {"Set-Cookie": "sid=; Max-Age=0"})
        if not self.authed():
            return self.redirect("/login")
        st = self.load()
        if path == "/expenses":
            rows = "".join(
                f'<tr data-id="{e["id"]}"><td>{e["id"]}</td><td>{html.escape(e["date"])}</td><td>{html.escape(e["vendor"])}</td><td>{html.escape(e["category"])}</td>'
                f'<td>{e["amount_usd"]:.2f}</td><td>{html.escape(e["receipt_ref"])}</td><td class="status">{e["status"]}</td><td><a href="/expenses/{e["id"]}">open</a></td></tr>'
                for e in st["expenses"])
            return self.page(f'<table border="1"><tr><th>ID</th><th>Date</th><th>Vendor</th><th>Category</th><th>Amount USD</th><th>Receipt</th><th>Status</th><th></th></tr>{rows}</table>')
        if path == "/expenses/new":
            opts = "".join(f"<option>{c}</option>" for c in CATEGORIES)
            return self.page('<h2>New expense</h2><form method="post" action="/expenses/new">'
                             '<label>Date (YYYY-MM-DD) <input name="date"></label><br><label>Vendor <input name="vendor"></label><br>'
                             f'<label>Category <select name="category">{opts}</select></label><br>'
                             '<label>Amount in USD <input name="amount_usd"></label><br><label>Receipt reference (for example R-01) <input name="receipt_ref"></label><br>'
                             '<label>Note <textarea name="note"></textarea></label><br><button type="submit">Save as draft</button></form>')
        parts = path.strip("/").split("/")
        if len(parts) == 2 and parts[0] == "expenses" and parts[1].isdigit():
            e = next((x for x in st["expenses"] if x["id"] == int(parts[1])), None)
            if not e:
                return self.page("<p>Not found</p>", 404)
            sub = ('<form method="post" action="/expenses/%d/submit"><button type="submit">Submit to finance</button></form>' % e["id"]) if e["status"] == "draft" else "<p>Submitted. This cannot be changed.</p>"
            return self.page(f'<h2>Expense {e["id"]} <span class="status">{e["status"]}</span></h2><p>{html.escape(e["date"])} | {html.escape(e["vendor"])} | {html.escape(e["category"])} | {e["amount_usd"]:.2f} USD | {html.escape(e["receipt_ref"])}</p>'
                             f'<p>Note: {html.escape(e.get("note", ""))}</p><p class="comment">Comment: {html.escape(e.get("comment", ""))}</p>'
                             + ('<form method="post" action="/expenses/%d/comment"><label>Comment <textarea name="comment"></textarea></label><button type="submit">Save comment</button></form>' % e["id"] if e["status"] == "draft" else "") + sub)
        if len(parts) == 3 and parts[0] == "expenses" and parts[2] == "confirm":
            return self.page(f'<h2>Confirm</h2><p>Submit expense {parts[1]} to finance? This cannot be undone.</p><form method="post" action="/expenses/{parts[1]}/confirm"><button type="submit">Confirm submit</button></form>')
        self.page("<p>Not found</p>", 404)

    def do_POST(self):
        path = self.path.split("?")[0]
        f = self.form()
        if path == "/login":
            if f.get("username") == USER and f.get("password") == PASSWORD:
                sid = secrets.token_hex(8); ERP.sessions.add(sid)
                return self.redirect("/expenses", {"Set-Cookie": f"sid={sid}; Path=/"})
            return self.page("<p>Wrong user or password.</p>", 401)
        if not self.authed():
            return self.redirect("/login")
        with LOCK:
            st = self.load()
            if path == "/expenses/new":
                try:
                    amt = float(f.get("amount_usd", "").replace(",", ""))
                except ValueError:
                    return self.page("<p>Amount must be a number.</p>", 400)
                if f.get("category") not in CATEGORIES or not f.get("date") or not f.get("vendor"):
                    return self.page("<p>Date, vendor and a valid category are required.</p>", 400)
                e = {"id": st["next_id"], "date": f["date"], "vendor": f["vendor"], "category": f["category"], "amount_usd": amt,
                     "receipt_ref": f.get("receipt_ref", ""), "note": f.get("note", ""), "comment": "", "status": "draft"}
                st["next_id"] += 1; st["expenses"].append(e); self.save(st)
                return self.redirect(f"/expenses/{e['id']}")
            parts = path.strip("/").split("/")
            if len(parts) == 3 and parts[0] == "expenses" and parts[1].isdigit():
                e = next((x for x in st["expenses"] if x["id"] == int(parts[1])), None)
                if not e:
                    return self.page("<p>Not found</p>", 404)
                if parts[2] == "comment" and e["status"] == "draft":
                    e["comment"] = f.get("comment", ""); self.save(st); return self.redirect(f"/expenses/{e['id']}")
                if parts[2] == "submit" and e["status"] == "draft":
                    return self.redirect(f"/expenses/{e['id']}/confirm")
                if parts[2] == "confirm" and e["status"] == "draft":
                    e["status"] = "submitted"; self.save(st); return self.redirect("/expenses")
        self.page("<p>Not allowed</p>", 400)


def serve(state_dir):
    os.makedirs(state_dir, exist_ok=True)
    ERP.state_path = os.path.join(state_dir, "erp.json")
    srv = ThreadingHTTPServer(("127.0.0.1", 8910), ERP)
    print("mock ERP on 8910", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "serve":
        serve(sys.argv[2])
