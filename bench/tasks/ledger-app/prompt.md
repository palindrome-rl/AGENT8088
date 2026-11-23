Build an expense tracker in a folder called `ledger/`. Use only the Python standard library (`http.server`, `sqlite3`, `json`, `csv`, `decimal`), because packages cannot be installed here.

Server (`ledger/server.py`): listens on the port in the `PORT` environment variable (8000 if it is not set), started with `python server.py` from inside `ledger/`. It keeps its data in a SQLite file `ledger.db` next to `server.py`, so expenses are still there after the server is restarted. Money is stored as whole cents, never as floating point. JSON API:

- `POST /api/expenses` with `{"date": "2026-03-05", "amount": "12.50", "category": "food", "note": "lunch"}` creates an expense and returns it with status 201 as `{"id", "date", "amount_cents", "category", "note"}` (here `amount_cents` is 1250). `amount` may be a string or a number with at most 2 decimals and must be greater than 0. `date` must be a real calendar date in `YYYY-MM-DD` form. `category` must not be empty after trimming spaces and is stored lower-case. `note` is optional and defaults to an empty string. Anything invalid gives status 400 and `{"error": "..."}`.
- `GET /api/expenses` lists the expenses ordered by date and then by id. `?from=` and `?to=` (both `YYYY-MM-DD`, both inclusive) limit the dates, and `?category=` limits the category, ignoring case. They can be combined. A malformed date gives 400.
- `DELETE /api/expenses/<id>` removes an expense and returns 204 with no body, or 404 with `{"error": "..."}`.
- `GET /api/summary?month=2026-03` returns `{"month": "2026-03", "count": n, "total_cents": n, "by_category": {"food": cents, ...}}` for the expenses dated in that month. A month with no expenses gives count 0, total 0 and `{}`. A malformed month gives 400.
- `GET /api/export.csv` returns all expenses as CSV (`Content-Type: text/csv`) with the header `id,date,amount,category,note`, one row per expense in the same order as the list, and the amount written as dollars with two decimals (`12.50`). Notes that contain commas, quotes or line breaks must be quoted correctly so that a CSV reader gets the original text back.
- `GET /` serves the page `ledger/public/index.html`; other files in `ledger/public/` are served as they are.

Page (`ledger/public/index.html`, with any script or stylesheet it needs): a single page with a form to add an expense, a table of expenses with amounts shown in dollars (`$12.50`) and a delete button on each row, filters for category and date range, the total of what is shown, and a summary for a chosen month that shows each category with its total and a bar whose length is proportional to its share. It uses the API above.

Also write `ledger/README.md` (how to run it and how to run the tests) and tests in `ledger/tests/` that run with `python -m unittest discover -s tests` from inside `ledger/`.

When you are done, tell me what you built and what you checked.
