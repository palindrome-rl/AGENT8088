# Notes

A small notes app built only with the Python standard library (`http.server`,
`sqlite3`, `json`). No third-party packages are required.

## Layout

```
notes/
  server.py          # HTTP server + JSON API
  notes.db           # SQLite database (created on first run)
  public/
    index.html       # single-page UI
  tests/
    test_api.py      # unittest suite for the API
```

## Running the server

From inside the `notes/` folder:

```bash
python server.py
```

The server listens on the port given by the `PORT` environment variable, or
`8000` if it is not set:

```bash
PORT=9000 python server.py
```

Then open <http://127.0.0.1:8000> (or your chosen port) in a browser.

Notes are stored in `notes.db` next to `server.py`, so they survive server
restarts. Delete that file to start fresh.

## API

All endpoints are JSON.

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/api/notes` | Create a note. Body: `{"title": str, "body": str, "tags": [str]}`. Returns `201` with `{"id", "title", "body", "tags"}`. Missing/empty title → `400` with `{"error": ...}`. |
| `GET` | `/api/notes` | List notes in id order. `?tag=x` keeps notes with tag `x`; `?q=text` keeps notes whose title or body contains `text` (case-insensitive); both together = intersection. |
| `GET` | `/api/notes/<id>` | One note, or `404` with `{"error": ...}`. |
| `PUT` | `/api/notes/<id>` | Replace title, body and tags (same validation as create). Returns the note, or `404`. |
| `DELETE` | `/api/notes/<id>` | Remove the note. Returns `204` with no body, or `404`. |
| `GET` | `/` | Serves `public/index.html`; other files under `public/` are served as-is. |

### Examples

```bash
curl -X POST http://127.0.0.1:8000/api/notes \
  -H 'Content-Type: application/json' \
  -d '{"title": "Groceries", "body": "milk, eggs", "tags": ["home"]}'

curl 'http://127.0.0.1:8000/api/notes?tag=home&q=milk'
curl http://127.0.0.1:8000/api/notes/1
curl -X PUT http://127.0.0.1:8000/api/notes/1 \
  -H 'Content-Type: application/json' \
  -d '{"title": "Groceries", "body": "milk, eggs, bread", "tags": ["home"]}'
curl -X DELETE http://127.0.0.1:8000/api/notes/1
```

## Running the tests

From inside the `notes/` folder:

```bash
python -m unittest discover -s tests
```

The tests start the server on a random free port with a temporary database and
public directory, so they never touch your real `notes.db`.
