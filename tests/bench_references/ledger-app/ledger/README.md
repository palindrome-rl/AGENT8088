# Ledger — Expense Tracker

A small expense tracker built with **only the Python standard library**
(`http.server`, `sqlite3`, `json`, `csv`, `decimal`). No packages to install.

Money is stored as **whole cents** (SQLite `INTEGER`) and never as floating
point, so totals are exact.

## Layout

```
ledger/
├── server.py            # HTTP server + JSON API
├── ledger.db            # SQLite database (created on first run)
├── public/
│   └── index.html       # single-page UI
└── tests/
    ├── test_api.py      # end-to-end API tests (real server, temp DB)
    └── test_units.py    # unit tests for the parsing helpers
```

## Running

From inside the `ledger/` directory:

```
python server.py
```

The server listens on the port in the `PORT` environment variable, or `8000`
if it is not set:

```
PORT=9000 python server.py          # Linux / macOS
set PORT=9000 && python server.py   # Windows
```

Then open <http://127.0.0.1:8000/> in a browser.

Data lives in `ledger.db` next to `server.py`, so expenses survive server
restarts. Delete the file to start fresh.

## API

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/expenses` | Create an expense. Body: `{"date": "YYYY-MM-DD", "amount": "12.50", "category": "food", "note": "lunch"}`. `amount` may be a string or number with at most 2 decimals and must be > 0. `category` is trimmed and stored lower-case. `note` is optional (defaults to `""`). Returns `201` with `{"id", "date", "amount_cents", "category", "note"}`; invalid input returns `400` with `{"error": "..."}`. |
| `GET` | `/api/expenses` | List expenses ordered by date, then id. Optional filters (combinable): `?from=YYYY-MM-DD`, `?to=YYYY-MM-DD` (both inclusive), `?category=` (case-insensitive). Malformed dates give `400`. |
| `DELETE` | `/api/expenses/<id>` | Delete an expense. `204` with no body, or `404` with `{"error": "..."}`. |
| `GET` | `/api/summary?month=YYYY-MM` | `{"month", "count", "total_cents", "by_category": {name: cents}}` for that month. Empty month gives count 0, total 0, `{}`. Malformed month gives `400`. |
| `GET` | `/api/export.csv` | All expenses as CSV (`text/csv`), header `id,date,amount,category,note`, same order as the list, amounts as dollars with two decimals. Notes with commas, quotes or newlines are quoted per RFC 4180. |
| `GET` | `/` | Serves `public/index.html`; other files under `public/` are served as-is. |

## Tests

From inside the `ledger/` directory:

```
python -m unittest discover -s tests
```

The API tests start a real server on a free port against a temporary
database, so they never touch your real `ledger.db`.
