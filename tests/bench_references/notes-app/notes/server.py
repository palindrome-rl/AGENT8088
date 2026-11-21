"""Notes app server: stdlib-only JSON API over SQLite, plus static files."""
import json
import os
import re
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "notes.db")
PUBLIC_DIR = os.path.join(BASE_DIR, "public")
PORT = int(os.environ.get("PORT", "8000"))

ID_RE = re.compile(r"^/api/notes/(\d+)$")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    conn.execute(
        """CREATE TABLE IF NOT EXISTS notes (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               title TEXT NOT NULL,
               body TEXT NOT NULL DEFAULT '',
               tags TEXT NOT NULL DEFAULT '[]'
           )"""
    )
    conn.commit()
    conn.close()


def row_to_note(row):
    return {
        "id": row["id"],
        "title": row["title"],
        "body": row["body"],
        "tags": json.loads(row["tags"]),
    }


def validate(payload):
    """Return (note_dict, None) or (None, error_message)."""
    if not isinstance(payload, dict):
        return None, "body must be a JSON object"
    title = payload.get("title")
    if not isinstance(title, str) or not title.strip():
        return None, "title is required and must be a non-empty string"
    body = payload.get("body", "")
    if not isinstance(body, str):
        return None, "body must be a string"
    tags = payload.get("tags", [])
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        return None, "tags must be a list of strings"
    return {"title": title, "body": body, "tags": tags}, None


class Handler(BaseHTTPRequestHandler):
    server_version = "NotesApp/1.0"

    # --- helpers ---------------------------------------------------------
    def send_json(self, status, obj):
        data = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None

    def serve_static(self, path):
        if path == "/":
            path = "/index.html"
        rel = path.lstrip("/")
        full = os.path.normpath(os.path.join(PUBLIC_DIR, rel))
        if not full.startswith(PUBLIC_DIR) or not os.path.isfile(full):
            self.send_json(404, {"error": "not found"})
            return
        ext = os.path.splitext(full)[1].lower()
        types = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".json": "application/json",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".svg": "image/svg+xml",
            ".ico": "image/x-icon",
        }
        with open(full, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", types.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # --- API -------------------------------------------------------------
    def handle_api(self, method):
        parsed = urlparse(self.path)
        path = parsed.path
        m = ID_RE.match(path)

        if path == "/api/notes" and method == "GET":
            qs = parse_qs(parsed.query)
            tag = qs.get("tag", [None])[0]
            q = qs.get("q", [None])[0]
            conn = get_db()
            rows = conn.execute("SELECT * FROM notes ORDER BY id").fetchall()
            conn.close()
            notes = [row_to_note(r) for r in rows]
            if tag is not None:
                notes = [n for n in notes if tag in n["tags"]]
            if q is not None:
                needle = q.lower()
                notes = [
                    n for n in notes
                    if needle in n["title"].lower() or needle in n["body"].lower()
                ]
            self.send_json(200, notes)
            return

        if path == "/api/notes" and method == "POST":
            payload = self.read_json()
            if payload is None:
                self.send_json(400, {"error": "invalid JSON body"})
                return
            note, err = validate(payload)
            if err:
                self.send_json(400, {"error": err})
                return
            conn = get_db()
            cur = conn.execute(
                "INSERT INTO notes (title, body, tags) VALUES (?, ?, ?)",
                (note["title"], note["body"], json.dumps(note["tags"])),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM notes WHERE id = ?", (cur.lastrowid,)
            ).fetchone()
            conn.close()
            self.send_json(201, row_to_note(row))
            return

        if m:
            note_id = int(m.group(1))
            conn = get_db()
            row = conn.execute(
                "SELECT * FROM notes WHERE id = ?", (note_id,)
            ).fetchone()

            if method == "GET":
                if row is None:
                    conn.close()
                    self.send_json(404, {"error": "note not found"})
                    return
                conn.close()
                self.send_json(200, row_to_note(row))
                return

            if method == "PUT":
                if row is None:
                    conn.close()
                    self.send_json(404, {"error": "note not found"})
                    return
                payload = self.read_json()
                if payload is None:
                    conn.close()
                    self.send_json(400, {"error": "invalid JSON body"})
                    return
                note, err = validate(payload)
                if err:
                    conn.close()
                    self.send_json(400, {"error": err})
                    return
                conn.execute(
                    "UPDATE notes SET title = ?, body = ?, tags = ? WHERE id = ?",
                    (note["title"], note["body"], json.dumps(note["tags"]), note_id),
                )
                conn.commit()
                row = conn.execute(
                    "SELECT * FROM notes WHERE id = ?", (note_id,)
                ).fetchone()
                conn.close()
                self.send_json(200, row_to_note(row))
                return

            if method == "DELETE":
                if row is None:
                    conn.close()
                    self.send_json(404, {"error": "note not found"})
                    return
                conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
                conn.commit()
                conn.close()
                self.send_response(204)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

        self.send_json(404, {"error": "not found"})

    # --- HTTP verbs --------------------------------------------------------
    def do_GET(self):
        if self.path.startswith("/api/"):
            self.handle_api("GET")
        else:
            self.serve_static(urlparse(self.path).path)

    def do_POST(self):
        self.handle_api("POST")

    def do_PUT(self):
        self.handle_api("PUT")

    def do_DELETE(self):
        self.handle_api("DELETE")

    def log_message(self, fmt, *args):
        pass  # keep test output quiet


def main():
    init_db()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Notes server running on http://127.0.0.1:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
