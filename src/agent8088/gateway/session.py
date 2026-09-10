import json
import os
import tempfile
from pathlib import Path
from typing import Optional
from urllib.parse import quote, unquote


def build_session_key(
    platform: str,
    chat_type: str,
    chat_id: str,
    thread_id: Optional[str] = None,
) -> str:
    parts = ["agent", "main", platform, chat_type, chat_id]
    if thread_id:
        parts.append(thread_id)
    return ":".join(parts)


class SessionStore:
    """Per-chat JSON session files."""

    def __init__(self, base_dir: str = None):
        home = Path(os.environ.get("AGENT8088_HOME", "~/.agent8088")).expanduser()
        self.dir = Path(base_dir).expanduser() if base_dir else home / "gateway-sessions"
        self.dir.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            os.chmod(self.dir, 0o700)

    def _path(self, key: str) -> Path:
        # ponytail: keys contain ":" which is illegal in Windows filenames;
        # percent-encode the stem, decode in list_all. Stdlib, reversible.
        return self.dir / f"{quote(key, safe='')}.json"

    def load(self, key: str) -> list:
        p = self._path(key)
        if not p.exists():
            return []
        payload = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(payload, list):  # Legacy session files.
            return payload
        return payload.get("messages", []) if isinstance(payload, dict) else []

    def load_trajectory(self, key: str) -> dict:
        p = self._path(key)
        if not p.exists():
            return {}
        payload = json.loads(p.read_text(encoding="utf-8"))
        state = payload.get("trajectory_state", {}) if isinstance(payload, dict) else {}
        return state if isinstance(state, dict) else {}

    def save(self, key: str, messages: list, trajectory_state: dict | None = None) -> None:
        fd, temporary = tempfile.mkstemp(prefix=".session-", suffix=".tmp", dir=self.dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                payload = (messages if trajectory_state is None else {
                    "version": 2,
                    "messages": messages,
                    "trajectory_state": trajectory_state,
                })
                json.dump(payload, stream, ensure_ascii=False)
            os.replace(temporary, self._path(key))
        finally:
            Path(temporary).unlink(missing_ok=True)

    def clear(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def list_all(self) -> list:
        return [unquote(p.stem) for p in self.dir.glob("*.json")]
