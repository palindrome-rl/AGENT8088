"""Expense tracker server.

Standard library only: http.server, sqlite3, json, csv, decimal.
Money is stored as whole cents (INTEGER) in SQLite, never as floats.
"""

import csv
import io
import json
import os
import re
import sqlite3
from datetime import date
from decimal import Decimal, InvalidOperation
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "ledger.db")
PUBLIC_DIR = os.path.join(BASE_DIR, "public")

MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")


def parse_date(value):
    """Return a date for a valid YYYY-MM-DD string, else None."""
    if not isinstance(value, str):
        return None
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", value)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def parse_month(value):
    """Return (year, month) for a valid YYYY-MM string, else None."""
    if not isinstance(value, str):
        return None
    m = MONTH_RE.match(value)
    if not m:
        return None
    year, month = int(m.group(1)), int(m.group(2))
    if not 1 <= month <= 12:
        return None
    return (year, month)


def parse_amount(value):
    """Parse a string or number with at most 2 decimals into cents.

    Returns (cents, None) on success or (None, error_message) on failure.
    """
    if isinstance(value, bool):
        return None, "amount must be a positive number with at most 2 decimals"
    if isinstance(value, (int, float)):
        try:
            text = repr(value)
        except Exception:
            return None, "amount must be a positive number with at most 2 decimals"
    elif isinstance(value, str):
        text = value.strip()
    else:
        return None, "amount must be a positive number with at most 2 decimals"
    if not text:
        return None, "amount must be a positive number with at most 2 decimals"
    try:
        dec = Decimal(text)
    except InvalidOperation:
        return None, "amount must be a positive number with at most 2 decimals"
    if not dec.is_finite():
        return None, "amount must be a positive number with at most 2 decimals"
    if dec <= 0:
        return None, "amount must be greater than 0"
    if dec != dec.quantize(Decimal("0.01")):
        return None, "amount must have at most 2 decimal places"
    return int((dec * 100).to_integral_value()), None


def init_db(conn):
    conn.execute(
        """CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            amount_cents INTEGER NOT NULL,
            category TEXT NOT NULL,
            note TEXT NOT NULL DEFAULT ''
        )"""
    )
    conn.commit()


def expense_row(row):
    return {
        "id": row[0],
        "date": row[1],
        "amount_cents": row[2],
        "category": row[3],
        "note": row[4],
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "Ledger/1.0"

    # --- helpers ---------------------------------------------------------

    def send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, status, message):
        self.send_json(status, {"error": message})

    def read_json_body(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None, "invalid Content-Length"
        if length < 0 or length > 1_000_000:
            return None, "request body too large"
        raw = self.rfile.read(length) if length else b""
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None, "request body must be valid JSON"
        if not isinstance(data, dict):
            return None, "request body must be a JSON object"
        return data, None

    def open_db(self):
        conn = sqlite3.connect(DB_PATH)
        init_db(conn)
        return conn

    # --- routing ---------------------------------------------------------

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/expenses":
            self.handle_list(parse_qs(parsed.query))
        elif path == "/api/summary":
            self.handle_summary(parse_qs(parsed.query))
        elif path == "/api/export.csv":
            self.handle_export()
        elif path == "/" or path.startswith("/public/"):
            self.handle_static(path)
        else:
            self.send_error_json(404, "not found")

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/expenses":
            self.handle_create()
        else:
            self.send_error_json(404, "not found")

    def do_DELETE(self):
        parsed = urlparse(self.path)
        m = re.match(r"^/api/expenses/(\d+)$", parsed.path)
        if m:
            self.handle_delete(int(m.group(1)))
        else:
            self.send_error_json(404, "not found")

    # --- API handlers ------------------------------------------------------

    def handle_create(self):
        data, err = self.read_json_body()
        if err:
            self.send_error_json(400, err)
            return

        date_value = data.get("date")
        d = parse_date(date_value)
        if d is None:
            self.send_error_json(400, "date must be a valid date in YYYY-MM-DD form")
            return

        cents, err = parse_amount(data.get("amount"))
        if err:
            self.send_error_json(400, err)
            return

        category = data.get("category")
        if not isinstance(category, str) or not category.strip():
            self.send_error_json(400, "category must be a non-empty string")
            return
        category = category.strip().lower()

        note = data.get("note", "")
        if note is None:
            note = ""
        if not isinstance(note, str):
            self.send_error_json(400, "note must be a string")
            return

        conn = self.open_db()
        try:
            cur = conn.execute(
                "INSERT INTO expenses (date, amount_cents, category, note) "
                "VALUES (?, ?, ?, ?)",
                (d.isoformat(), cents, category, note),
            )
            conn.commit()
            row = conn.execute(
                "SELECT id, date, amount_cents, category, note FROM expenses WHERE id = ?",
                (cur.lastrowid,),
            ).fetchone()
        finally:
            conn.close()
        self.send_json(201, expense_row(row))

    def handle_list(self, query):
        where = []
        params = []

        for key in ("from", "to"):
            values = query.get(key)
            if values:
                d = parse_date(values[0])
                if d is None:
                    self.send_error_json(400, "%s must be a valid date in YYYY-MM-DD form" % key)
                    return
                where.append("date %s ?" % (">=" if key == "from" else "<="))
                params.append(d.isoformat())

        category_values = query.get("category")
        if category_values:
            category = category_values[0].strip()
            if not category:
                self.send_error_json(400, "category must not be empty")
                return
            where.append("LOWER(category) = ?")
            params.append(category.lower())

        sql = "SELECT id, date, amount_cents, category, note FROM expenses"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY date, id"

        conn = self.open_db()
        try:
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
        self.send_json(200, [expense_row(r) for r in rows])

    def handle_delete(self, expense_id):
        conn = self.open_db()
        try:
            cur = conn.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))
            conn.commit()
            deleted = cur.rowcount
        finally:
            conn.close()
        if deleted:
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self.send_error_json(404, "expense %d not found" % expense_id)

    def handle_summary(self, query):
        values = query.get("month")
        if not values:
            self.send_error_json(400, "month is required (YYYY-MM)")
            return
        ym = parse_month(values[0])
        if ym is None:
            self.send_error_json(400, "month must be a valid month in YYYY-MM form")
            return
        year, month = ym
        prefix = "%04d-%02d" % (year, month)

        conn = self.open_db()
        try:
            rows = conn.execute(
                "SELECT category, SUM(amount_cents), COUNT(*) FROM expenses "
                "WHERE date LIKE ? || '-%' GROUP BY category",
                (prefix,),
            ).fetchall()
        finally:
            conn.close()

        by_category = {}
        total = 0
        count = 0
        for category, cents, n in rows:
            by_category[category] = cents
            total += cents
            count += n
        self.send_json(200, {
            "month": prefix,
            "count": count,
            "total_cents": total,
            "by_category": by_category,
        })

    def handle_export(self):
        conn = self.open_db()
        try:
            rows = conn.execute(
                "SELECT id, date, amount_cents, category, note FROM expenses "
                "ORDER BY date, id"
            ).fetchall()
        finally:
            conn.close()

        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\n")
        writer.writerow(["id", "date", "amount", "category", "note"])
        for row in rows:
            dollars = "%d.%02d" % (row[2] // 100, row[2] % 100)
            writer.writerow([row[0], row[1], dollars, row[3], row[4]])
        body = buf.getvalue().encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "text/csv")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # --- static files ------------------------------------------------------

    def handle_static(self, path):
        if path == "/":
            rel = "index.html"
        else:
            rel = path[len("/public/"):]
        rel = rel.replace("\\", "/")
        if rel.startswith("/") or ".." in rel.split("/"):
            self.send_error_json(404, "not found")
            return
        full = os.path.normpath(os.path.join(PUBLIC_DIR, rel))
        if not full.startswith(PUBLIC_DIR + os.sep) and full != PUBLIC_DIR:
            self.send_error_json(404, "not found")
            return
        if not os.path.isfile(full):
            self.send_error_json(404, "not found")
            return
        with open(full, "rb") as f:
            body = f.read()
        if full.endswith(".html"):
            content_type = "text/html; charset=utf-8"
        elif full.endswith(".css"):
            content_type = "text/css; charset=utf-8"
        elif full.endswith(".js"):
            content_type = "application/javascript; charset=utf-8"
        else:
            content_type = "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass  # keep test output quiet


def main():
    port = 8000
    raw = os.environ.get("PORT")
    if raw:
        try:
            port = int(raw)
        except ValueError:
            pass
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print("Ledger server listening on http://127.0.0.1:%d" % port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
