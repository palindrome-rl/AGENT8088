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
    # chat_id and thread_id are percent-encoded individually: the ":" join made
    # chat_id="a", thread="b:c" collide with chat_id="a:b", thread="c" (audit L9).
    parts = ["agent", "main", platform, chat_type, quote(chat_id, safe="")]
    if thread_id:
        parts.append(quote(thread_id, safe=""))
    return ":".join(parts)


class SessionStore:
    """Per-chat JSON session files."""

    def __init__(self, base_dir: Optional[str] = None):
        home = Path(os.environ.get("AGENT8088_HOME", "~/.agent8088")).expanduser()
        self.dir = Path(base_dir).expanduser() if base_dir else home / "gateway-sessions"
        self.dir.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            os.chmod(self.dir, 0o700)
        # Cleared-once counter per key: a turn that loaded before /new must not
        # write its transcript back over the cleared file (audit L4).
        self._generation: dict = {}

    def _path(self, key: str) -> Path:
        # ponytail: keys contain ":" which is illegal in Windows filenames;
        # percent-encode the stem, decode in list_all. Stdlib, reversible.
        return self.dir / f"{quote(key, safe='')}.json"

    def generation(self, key: str) -> int:
        return self._generation.get(key, 0)

    def _read_json(self, p: Path):
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            # A corrupt file must not brick the chat with "[error: ...]" on
            # every message until /new (audit L5): set it aside, start clean.
            try:
                p.rename(p.with_suffix(p.suffix + ".corrupt"))
            except OSError:
                p.unlink(missing_ok=True)
            return None

    def load(self, key: str) -> list:
        p = self._path(key)
        if not p.exists():
            return []
        payload = self._read_json(p)
        if payload is None:
            return []
        if isinstance(payload, list):  # Legacy session files.
            return payload
        return payload.get("messages", []) if isinstance(payload, dict) else []

    def load_trajectory(self, key: str) -> dict:
        p = self._path(key)
        if not p.exists():
            return {}
        payload = self._read_json(p)
        if payload is None:
            return {}
        state = payload.get("trajectory_state", {}) if isinstance(payload, dict) else {}
        return state if isinstance(state, dict) else {}

    def save(self, key: str, messages: list, trajectory_state: dict | None = None,
             generation: int | None = None) -> None:
        if generation is not None and generation != self._generation.get(key, 0):
            return  # /new happened mid-turn; don't resurrect the session.
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
        self._generation[key] = self._generation.get(key, 0) + 1

    def list_all(self) -> list:
        return [unquote(p.stem) for p in self.dir.glob("*.json")]