"""Grader for http-api-server.

    python grade.py <workspace> --json <report.json>

Starts server.py itself on a free port with an empty notes.json, exercises every
endpoint over HTTP, restarts it to check that notes persist and ids are not
reused, and stops it. The agent's own notes.json is put back afterwards.
"""
import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _request(port, method, path, body=None, raw=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method,
                                     headers={"Content-Type": "application/json"} if data is not None else {})
    try:
        with urllib.request.urlopen(request, timeout=10) as answer:
            status, text, kind = answer.status, answer.read().decode(), answer.headers.get("Content-Type", "")
    except urllib.error.HTTPError as error:
        status, text, kind = error.code, error.read().decode(), error.headers.get("Content-Type", "")
    try:
        payload = json.loads(text) if text else None
    except ValueError:
        payload = "<not json>"
    return status, payload, kind


class Server:
    def __init__(self, workspace: Path, port: int):
        self.process = subprocess.Popen([sys.executable, "server.py"], cwd=workspace,
                                        env={**os.environ, "PORT": str(port)},
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.port = port

    def ready(self, seconds=15) -> bool:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self.process.poll() is not None:
                return False
            try:
                if _request(self.port, "GET", "/health")[0] == 200:
                    return True
            except OSError:
                pass
            time.sleep(0.3)
        return False

    def stop(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()


def grade(workspace: Path) -> dict:
    checks = {}
    if not (workspace / "server.py").is_file():
        return {"passed": False, "score": 0.0, "checks": {"server.py exists": False}}
    store = workspace / "notes.json"
    theirs = store.read_bytes() if store.is_file() else None
    store.unlink(missing_ok=True)
    port = _free_port()
    server = Server(workspace, port)
    try:
        checks["starts and answers /health"] = server.ready() and _request(port, "GET", "/health")[1] == {"status": "ok"}
        if not checks["starts and answers /health"]:
            return _report(checks, total=11)
        status, first, kind = _request(port, "POST", "/notes", {"text": "buy milk"})
        checks["POST creates a note with 201"] = status == 201 and isinstance(first, dict) \
            and first.get("text") == "buy milk" and isinstance(first.get("id"), int)
        checks["answers are JSON"] = "application/json" in kind
        status, second, _ = _request(port, "POST", "/notes", {"text": "call home"})
        bad = [_request(port, "POST", "/notes", body)[0] for body in ({}, {"text": ""}, {"text": 5})]
        bad.append(_request(port, "POST", "/notes", raw=b"not json")[0])
        checks["bad bodies answer 400"] = bad == [400, 400, 400, 400]
        status, listed, _ = _request(port, "GET", "/notes")
        checks["GET /notes lists oldest first"] = status == 200 and isinstance(listed, list) \
            and [n.get("text") for n in listed] == ["buy milk", "call home"]
        ok = isinstance(first, dict) and isinstance(second, dict)
        status, one, _ = _request(port, "GET", f"/notes/{first['id']}") if ok else (0, None, "")
        checks["GET /notes/<id> answers the note"] = status == 200 and one == first
        checks["a missing note answers 404"] = _request(port, "GET", "/notes/999999")[0] == 404
        deleted = _request(port, "DELETE", f"/notes/{first['id']}")[0] if ok else 0
        again = _request(port, "DELETE", f"/notes/{first['id']}")[0] if ok else 0
        checks["DELETE answers 204, then 404"] = (deleted, again) == (204, 404)

        server.stop()
        server = Server(workspace, port)
        restarted = server.ready()
        status, listed, _ = _request(port, "GET", "/notes") if restarted else (0, None, "")
        checks["notes persist across a restart"] = status == 200 and isinstance(listed, list) \
            and [n.get("text") for n in listed] == ["call home"]
        status, third, _ = _request(port, "POST", "/notes", {"text": "third"}) if restarted else (0, None, "")
        used = {first.get("id"), second.get("id")} if ok else set()
        checks["ids are not reused"] = status == 201 and isinstance(third, dict) and third.get("id") not in used
        checks["stores notes in notes.json"] = store.is_file()
    finally:
        server.stop()
        store.unlink(missing_ok=True)
        if theirs is not None:
            store.write_bytes(theirs)
    return _report(checks)


def _report(checks, total=None):
    total = total or len(checks)
    score = sum(checks.values()) / total
    return {"passed": len(checks) == total and all(checks.values()), "score": round(score, 3), "checks": checks}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("workspace")
    parser.add_argument("--json", required=True)
    args = parser.parse_args()
    report = grade(Path(args.workspace))
    Path(args.json).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    sys.exit(main())
