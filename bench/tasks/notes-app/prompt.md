Build a notes app in a folder called `notes/`. Use only the Python standard library (`http.server`, `sqlite3`, `json`), because packages cannot be installed here.

Server (`notes/server.py`): listens on the port in the `PORT` environment variable (8000 if it is not set), started with `python server.py` from inside `notes/`. It keeps its data in a SQLite file `notes.db` next to `server.py`, so notes are still there after the server is restarted. JSON API:

- `POST /api/notes` with `{"title": ..., "body": ..., "tags": [...]}` creates a note and returns it with status 201 as `{"id", "title", "body", "tags"}`. A missing or empty title gives status 400 and `{"error": "..."}`.
- `GET /api/notes` lists the notes in id order. `?tag=x` keeps the notes that have the tag `x`; `?q=text` keeps the notes whose title or body contains the text, ignoring case; both together keep the notes that match both.
- `GET /api/notes/<id>` returns one note, or status 404 and `{"error": "..."}` when there is no such note.
- `PUT /api/notes/<id>` replaces the title, body and tags (same rules as creating) and returns the note, or 404.
- `DELETE /api/notes/<id>` removes the note and returns status 204 with no body, or 404.
- `GET /` serves the page `notes/public/index.html`; other files in `notes/public/` are served as they are.

Page (`notes/public/index.html`, with any script or stylesheet it needs): a single page that lists the notes, adds a note (title, body, comma-separated tags), edits and deletes a note, filters by tag and searches by text, using the API above.

Also write `notes/README.md` (how to run it and how to run the tests) and tests in `notes/tests/` that run with `python -m unittest discover -s tests` from inside `notes/`.

When you are done, tell me what you built and what you checked.
