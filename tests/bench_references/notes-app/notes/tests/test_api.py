"""API tests for the notes server.

Run from inside notes/ with:
    python -m unittest discover -s tests
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import server  # noqa: E402


class ApiTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp(prefix="notes-test-")
        cls.db_path = os.path.join(cls.tmpdir, "notes.db")
        cls.public_dir = os.path.join(cls.tmpdir, "public")
        os.makedirs(cls.public_dir)
        with open(os.path.join(cls.public_dir, "index.html"), "w") as f:
            f.write("<html><body>notes page</body></html>")
        with open(os.path.join(cls.public_dir, "style.css"), "w") as f:
            f.write("body { color: red; }")

        cls._orig_db = server.DB_PATH
        cls._orig_public = server.PUBLIC_DIR
        server.DB_PATH = cls.db_path
        server.PUBLIC_DIR = cls.public_dir
        server.init_db()

        cls.port = 0
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)
        server.DB_PATH = cls._orig_db
        server.PUBLIC_DIR = cls._orig_public
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def setUp(self):
        # start each test with an empty database
        conn = server.get_db()
        conn.execute("DELETE FROM notes")
        conn.execute("DELETE FROM sqlite_sequence WHERE name = 'notes'")
        conn.commit()
        conn.close()

    # --- helpers ---------------------------------------------------------
    def request(self, method, path, payload=None):
        url = self.base + path
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req) as res:
                body = res.read()
                return res.status, json.loads(body) if body else None
        except urllib.error.HTTPError as e:
            body = e.read()
            return e.code, json.loads(body) if body else None

    def create_note(self, title="T", body="B", tags=None):
        return self.request("POST", "/api/notes", {
            "title": title, "body": body, "tags": tags or [],
        })

    # --- POST ------------------------------------------------------------
    def test_create_note_response(self):
        status, note = self.create_note("Hello", "World", ["a", "b"])
        self.assertEqual(status, 201)
        self.assertIn("id", note)
        self.assertEqual(note["title"], "Hello")
        self.assertEqual(note["body"], "World")
        self.assertEqual(note["tags"], ["a", "b"])

    def test_create_note_defaults(self):
        status, note = self.request("POST", "/api/notes", {"title": "Only title"})
        self.assertEqual(status, 201)
        self.assertEqual(note["body"], "")
        self.assertEqual(note["tags"], [])

    def test_create_missing_title_rejected(self):
        status, body = self.request("POST", "/api/notes", {"body": "no title"})
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_create_empty_title_rejected(self):
        status, body = self.create_note("   ", "x")
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_create_non_string_title_rejected(self):
        status, body = self.request("POST", "/api/notes", {"title": 42})
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_create_bad_tags_rejected(self):
        status, body = self.request("POST", "/api/notes", {
            "title": "t", "tags": "not-a-list",
        })
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_create_invalid_json_rejected(self):
        req = urllib.request.Request(
            self.base + "/api/notes",
            data=b"{not json",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req) as res:
                status = res.status
        except urllib.error.HTTPError as e:
            status = e.code
        self.assertEqual(status, 400)

    # --- GET list ----------------------------------------------------------
    def test_list_empty(self):
        status, notes = self.request("GET", "/api/notes")
        self.assertEqual(status, 200)
        self.assertEqual(notes, [])

    def test_list_in_id_order(self):
        self.create_note("first")
        self.create_note("second")
        self.create_note("third")
        status, notes = self.request("GET", "/api/notes")
        self.assertEqual(status, 200)
        self.assertEqual([n["title"] for n in notes], ["first", "second", "third"])
        self.assertEqual(notes[0]["id"] < notes[1]["id"] < notes[2]["id"], True)

    def test_filter_by_tag(self):
        self.create_note("a", "x", ["red"])
        self.create_note("b", "y", ["blue"])
        self.create_note("c", "z", ["red", "blue"])
        status, notes = self.request("GET", "/api/notes?tag=red")
        self.assertEqual(status, 200)
        self.assertEqual([n["title"] for n in notes], ["a", "c"])

    def test_filter_by_tag_no_match(self):
        self.create_note("a", "x", ["red"])
        status, notes = self.request("GET", "/api/notes?tag=green")
        self.assertEqual(status, 200)
        self.assertEqual(notes, [])

    def test_search_by_text_case_insensitive(self):
        self.create_note("Apple pie", "a dessert")
        self.create_note("Banana split", "another dessert")
        self.create_note("Car", "a vehicle")
        status, notes = self.request("GET", "/api/notes?q=DESSERT")
        self.assertEqual(status, 200)
        self.assertEqual([n["title"] for n in notes], ["Apple pie", "Banana split"])

    def test_search_matches_title_or_body(self):
        self.create_note("unique title", "plain body")
        self.create_note("plain title", "unique body")
        status, notes = self.request("GET", "/api/notes?q=unique")
        self.assertEqual(status, 200)
        self.assertEqual(len(notes), 2)

    def test_tag_and_q_combined(self):
        self.create_note("a", "alpha", ["red"])
        self.create_note("b", "beta", ["red"])
        self.create_note("c", "alpha", ["blue"])
        status, notes = self.request("GET", "/api/notes?tag=red&q=alpha")
        self.assertEqual(status, 200)
        self.assertEqual([n["title"] for n in notes], ["a"])

    # --- GET one -----------------------------------------------------------
    def test_get_one(self):
        _, created = self.create_note("Solo", "body", ["t"])
        status, note = self.request("GET", f"/api/notes/{created['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(note, created)

    def test_get_one_missing_not_found(self):
        status, body = self.request("GET", "/api/notes/9999")
        self.assertEqual(status, 404)
        self.assertIn("error", body)

    # --- PUT ---------------------------------------------------------------
    def test_put_replaces_fields(self):
        _, created = self.create_note("old", "old body", ["old"])
        status, note = self.request("PUT", f"/api/notes/{created['id']}", {
            "title": "new", "body": "new body", "tags": ["new"],
        })
        self.assertEqual(status, 200)
        self.assertEqual(note["title"], "new")
        self.assertEqual(note["body"], "new body")
        self.assertEqual(note["tags"], ["new"])
        self.assertEqual(note["id"], created["id"])

    def test_put_missing_not_found(self):
        status, body = self.request("PUT", "/api/notes/9999", {
            "title": "x", "body": "y", "tags": [],
        })
        self.assertEqual(status, 404)
        self.assertIn("error", body)

    def test_put_empty_title_rejected(self):
        _, created = self.create_note("keep")
        status, body = self.request("PUT", f"/api/notes/{created['id']}", {
            "title": "", "body": "y", "tags": [],
        })
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    # --- DELETE ------------------------------------------------------------
    def test_delete_removes_note(self):
        _, created = self.create_note("doomed")
        status, body = self.request("DELETE", f"/api/notes/{created['id']}")
        self.assertEqual(status, 204)
        self.assertIsNone(body)
        status, notes = self.request("GET", "/api/notes")
        self.assertEqual(notes, [])

    def test_delete_missing_not_found(self):
        status, body = self.request("DELETE", "/api/notes/9999")
        self.assertEqual(status, 404)
        self.assertIn("error", body)

    # --- static files --------------------------------------------------------
    def test_serves_index_at_root(self):
        req = urllib.request.Request(self.base + "/")
        with urllib.request.urlopen(req) as res:
            self.assertEqual(res.status, 200)
            self.assertIn("text/html", res.headers["Content-Type"])
            self.assertIn(b"notes page", res.read())

    def test_serves_other_static_file(self):
        req = urllib.request.Request(self.base + "/style.css")
        with urllib.request.urlopen(req) as res:
            self.assertEqual(res.status, 200)
            self.assertIn("text/css", res.headers["Content-Type"])
            self.assertIn(b"color: red", res.read())

    def test_missing_static_file_not_found(self):
        try:
            with urllib.request.urlopen(self.base + "/nope.txt") as res:
                status = res.status
        except urllib.error.HTTPError as e:
            status = e.code
        self.assertEqual(status, 404)

    def test_path_traversal_blocked(self):
        try:
            with urllib.request.urlopen(
                self.base + "/../server.py"
            ) as res:
                status = res.status
        except urllib.error.HTTPError as e:
            status = e.code
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
