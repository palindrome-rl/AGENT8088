Please write a small notes API in this folder, as `server.py`, using only the Python standard library (no packages to install).

- It listens on 127.0.0.1, on the port given by the `PORT` environment variable, or 8765 when that is not set.
- `GET /health` answers `{"status": "ok"}`.
- `POST /notes` with a JSON body `{"text": "..."}` creates a note and answers status 201 with `{"id": <int>, "text": "..."}`. A missing, empty or non-string `text`, or a body that is not JSON, answers status 400.
- `GET /notes` answers the list of all notes, oldest first.
- `GET /notes/<id>` answers one note, or status 404 when there is no such note.
- `DELETE /notes/<id>` deletes it and answers status 204, or 404 when there is no such note.
- Every answer with a body is JSON with the `Content-Type: application/json` header.
- Notes are kept in `notes.json` next to server.py, so they are still there after the server restarts. Ids are never reused.

Start the server, check every endpoint yourself with real HTTP requests, including a restart, and stop it again before you finish. Then tell me what you checked.
