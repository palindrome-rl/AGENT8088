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
        self.dir = Path(base_dir or "~/.agent8088/gateway-sessions").expanduser()
        self.dir.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            os.chmod(self.dir, 0o700)

    def _path(self, key: str) -> Path:
        # ponytail: keys contain ":" which is illegal in Windows filenames;
        # percent-encode the stem, decode in list_all. Stdlib, reversible.
        return self.dir / f"{quote(key, safe='')}.json"

    def load(self, key: str) -> list:
        p = self._path(key)
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []

    def save(self, key: str, messages: list) -> None:
        fd, temporary = tempfile.mkstemp(prefix=".session-", suffix=".tmp", dir=self.dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(messages, stream, ensure_ascii=False)
            os.replace(temporary, self._path(key))
        finally:
            Path(temporary).unlink(missing_ok=True)

    def clear(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def list_all(self) -> list:
        return [unquote(p.stem) for p in self.dir.glob("*.json")]
