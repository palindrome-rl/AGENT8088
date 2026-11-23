"""API tests for the ledger server.

Run from inside ledger/ with:
    python -m unittest discover -s tests
"""

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
LEDGER_DIR = os.path.dirname(HERE)
sys.path.insert(0, LEDGER_DIR)

import server  # noqa: E402


class ApiTestCase(unittest.TestCase):
    """Starts a real server on a free port against a temp database."""

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp(prefix="ledger-test-")
        cls.db_path = os.path.join(cls.tmpdir, "ledger.db")
        # Point the server module at the temp DB for the duration of the tests.
        cls._orig_db = server.DB_PATH
        server.DB_PATH = cls.db_path

        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)
        server.DB_PATH = cls._orig_db
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    # --- helpers ---------------------------------------------------------

    def url(self, path):
        return "http://127.0.0.1:%d%s" % (self.port, path)

    def request(self, method, path, body=None):
        data = None
        headers = {}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.url(path), data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req) as res:
                raw = res.read()
                return res.status, dict(res.headers), raw
        except urllib.error.HTTPError as e:
            raw = e.read()
            return e.code, dict(e.headers), raw

    def post_expense(self, **fields):
        return self.request("POST", "/api/expenses", fields)

    def get_json(self, path):
        status, headers, raw = self.request("GET", path)
        return status, json.loads(raw.decode("utf-8"))

    def seed(self, rows):
        """Insert rows directly into the DB: (date, amount_cents, category, note)."""
        conn = sqlite3.connect(self.db_path)
        server.init_db(conn)
        conn.executemany(
            "INSERT INTO expenses (date, amount_cents, category, note) VALUES (?, ?, ?, ?)",
            rows,
        )
        conn.commit()
        conn.close()

    def clear(self):
        conn = sqlite3.connect(self.db_path)
        server.init_db(conn)
        conn.execute("DELETE FROM expenses")
        conn.commit()
        conn.close()

    def setUp(self):
        self.clear()

    # --- POST /api/expenses ------------------------------------------------

    def test_create_returns_created_and_fields(self):
        status, _, raw = self.post_expense(
            date="2026-03-05", amount="12.50", category="food", note="lunch"
        )
        self.assertEqual(status, 201)
        body = json.loads(raw.decode("utf-8"))
        self.assertEqual(body["date"], "2026-03-05")
        self.assertEqual(body["amount_cents"], 1250)
        self.assertEqual(body["category"], "food")
        self.assertEqual(body["note"], "lunch")
        self.assertIsInstance(body["id"], int)

    def test_create_numeric_amount(self):
        status, _, raw = self.post_expense(date="2026-03-05", amount=12.5, category="food")
        self.assertEqual(status, 201)
        body = json.loads(raw.decode("utf-8"))
        self.assertEqual(body["amount_cents"], 1250)
        self.assertEqual(body["note"], "")

    def test_create_category_trimmed_and_lowercased(self):
        status, _, raw = self.post_expense(
            date="2026-03-05", amount="1.00", category="  Food  "
        )
        self.assertEqual(status, 201)
        self.assertEqual(json.loads(raw.decode("utf-8"))["category"], "food")

    def test_create_rejects_bad_dates(self):
        for bad in ("2026-02-30", "2026-13-01", "05-03-2026", "2026-3-5", "not-a-date", ""):
            status, _, raw = self.post_expense(date=bad, amount="1.00", category="food")
            self.assertEqual(status, 400, "date %r should be rejected" % bad)
            self.assertIn("error", json.loads(raw.decode("utf-8")))

    def test_create_rejects_bad_amounts(self):
        for bad in ("0", "-1.00", "1.234", "abc", "", None, True, "12.50.00"):
            status, _, raw = self.post_expense(date="2026-03-05", amount=bad, category="food")
            self.assertEqual(status, 400, "amount %r should be rejected" % (bad,))
            self.assertIn("error", json.loads(raw.decode("utf-8")))

    def test_create_rejects_empty_category(self):
        for bad in ("", "   ", None):
            status, _, raw = self.post_expense(date="2026-03-05", amount="1.00", category=bad)
            self.assertEqual(status, 400, "category %r should be rejected" % (bad,))

    def test_create_rejects_non_json_body(self):
        req = urllib.request.Request(
            self.url("/api/expenses"),
            data=b"not json",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req) as res:
                status = res.status
        except urllib.error.HTTPError as e:
            status = e.code
        self.assertEqual(status, 400)

    # --- GET /api/expenses ---------------------------------------------------

    def test_list_ordering(self):
        self.seed([
            ("2026-03-05", 100, "food", "a"),
            ("2026-03-01", 200, "food", "b"),
            ("2026-03-05", 300, "travel", "c"),
            ("2026-02-28", 400, "food", "d"),
        ])
        status, rows = self.get_json("/api/expenses")
        self.assertEqual(status, 200)
        self.assertEqual([r["date"] for r in rows],
                         ["2026-02-28", "2026-03-01", "2026-03-05", "2026-03-05"])
        # same date -> ordered by id
        self.assertEqual([r["note"] for r in rows[2:]], ["a", "c"])

    def test_list_date_filters_inclusive(self):
        self.seed([
            ("2026-03-01", 100, "food", ""),
            ("2026-03-05", 200, "food", ""),
            ("2026-03-10", 300, "food", ""),
        ])
        status, rows = self.get_json("/api/expenses?from=2026-03-01&to=2026-03-05")
        self.assertEqual(status, 200)
        self.assertEqual([r["date"] for r in rows], ["2026-03-01", "2026-03-05"])

    def test_list_category_filter_case_insensitive(self):
        self.seed([
            ("2026-03-01", 100, "Food", ""),
            ("2026-03-02", 200, "food", ""),
            ("2026-03-03", 300, "travel", ""),
        ])
        status, rows = self.get_json("/api/expenses?category=FOOD")
        self.assertEqual(status, 200)
        self.assertEqual(len(rows), 2)
        # filter is case-insensitive; stored values are returned as-is
        self.assertTrue(all(r["category"].lower() == "food" for r in rows))

    def test_list_combined_filters(self):
        self.seed([
            ("2026-03-01", 100, "food", ""),
            ("2026-03-05", 200, "food", ""),
            ("2026-03-05", 300, "travel", ""),
            ("2026-04-01", 400, "food", ""),
        ])
        status, rows = self.get_json("/api/expenses?from=2026-03-01&to=2026-03-31&category=food")
        self.assertEqual(status, 200)
        self.assertEqual([r["date"] for r in rows], ["2026-03-01", "2026-03-05"])

    def test_list_malformed_date_rejected(self):
        for bad in ("from=2026-02-30", "to=banana", "from=2026-3-5"):
            status, _, raw = self.request("GET", "/api/expenses?" + bad)
            self.assertEqual(status, 400, bad)
            self.assertIn("error", json.loads(raw.decode("utf-8")))

    # --- DELETE /api/expenses/<id> ---------------------------------------------

    def test_delete_existing_returns_no_content(self):
        status, _, raw = self.post_expense(date="2026-03-05", amount="1.00", category="food")
        expense_id = json.loads(raw.decode("utf-8"))["id"]
        status, headers, raw = self.request("DELETE", "/api/expenses/%d" % expense_id)
        self.assertEqual(status, 204)
        self.assertEqual(raw, b"")
        status, rows = self.get_json("/api/expenses")
        self.assertEqual(rows, [])

    def test_delete_missing_not_found(self):
        status, _, raw = self.request("DELETE", "/api/expenses/999999")
        self.assertEqual(status, 404)
        self.assertIn("error", json.loads(raw.decode("utf-8")))

    # --- GET /api/summary ---------------------------------------------------------

    def test_summary(self):
        self.seed([
            ("2026-03-01", 100, "food", ""),
            ("2026-03-05", 250, "food", ""),
            ("2026-03-09", 1000, "travel", ""),
            ("2026-04-01", 999, "food", ""),
        ])
        status, body = self.get_json("/api/summary?month=2026-03")
        self.assertEqual(status, 200)
        self.assertEqual(body["month"], "2026-03")
        self.assertEqual(body["count"], 3)
        self.assertEqual(body["total_cents"], 1350)
        self.assertEqual(body["by_category"], {"food": 350, "travel": 1000})

    def test_summary_empty_month(self):
        status, body = self.get_json("/api/summary?month=2026-01")
        self.assertEqual(status, 200)
        self.assertEqual(body, {"month": "2026-01", "count": 0, "total_cents": 0, "by_category": {}})

    def test_summary_malformed_month(self):
        for bad in ("2026-13", "2026-00", "2026-3", "202603", ""):
            status, _, raw = self.request("GET", "/api/summary?month=" + bad)
            self.assertEqual(status, 400, bad)
            self.assertIn("error", json.loads(raw.decode("utf-8")))

    # --- GET /api/export.csv ---------------------------------------------------------

    def test_export_csv(self):
        self.seed([
            ("2026-03-05", 1250, "food", "lunch"),
            ("2026-03-01", 50, "food", ""),
        ])
        status, headers, raw = self.request("GET", "/api/export.csv")
        self.assertEqual(status, 200)
        self.assertTrue(headers.get("Content-Type", "").startswith("text/csv"))
        text = raw.decode("utf-8")
        lines = text.split("\n")
        self.assertEqual(lines[0], "id,date,amount,category,note")
        self.assertEqual(len([l for l in lines if l]), 3)  # header + 2 rows
        # ordered by date then id: 2026-03-01 first
        self.assertIn("2026-03-01,0.50,food", lines[1])
        self.assertIn("2026-03-05,12.50,food,lunch", lines[2])

    def test_export_csv_quotes_special_notes(self):
        tricky = 'has, comma and "quotes" and\nnewline'
        self.seed([("2026-03-05", 100, "food", tricky)])
        status, _, raw = self.request("GET", "/api/export.csv")
        self.assertEqual(status, 200)
        import csv as csv_mod
        import io as io_mod
        reader = csv_mod.reader(io_mod.StringIO(raw.decode("utf-8")))
        rows = list(reader)
        self.assertEqual(rows[0], ["id", "date", "amount", "category", "note"])
        self.assertEqual(rows[1][4], tricky)

    # --- static files ---------------------------------------------------------------

    def test_index_served(self):
        status, headers, raw = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers.get("Content-Type", ""))
        self.assertIn(b"Expense Tracker", raw)

    def test_public_file_served(self):
        status, headers, raw = self.request("GET", "/public/index.html")
        self.assertEqual(status, 200)
        self.assertIn(b"Expense Tracker", raw)

    def test_missing_file_not_found(self):
        status, _, _ = self.request("GET", "/public/nope.txt")
        self.assertEqual(status, 404)

    def test_path_traversal_blocked(self):
        status, _, _ = self.request("GET", "/public/../server.py")
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
