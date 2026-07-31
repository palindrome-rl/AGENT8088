import json
import re
from pathlib import Path

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9@.+\-]+$")


def normalize_whatsapp_id(value: str) -> str:
    return (
        str(value or "")
        .strip()
        .replace("+", "", 1)
        .split(":", 1)[0]
        .split("@", 1)[0]
    )


def expand_whatsapp_aliases(identifier: str, session_dir: Path) -> set:
    normalized = normalize_whatsapp_id(identifier)
    if not normalized:
        return set()
    session_dir = Path(session_dir)
    resolved = set()
    queue = [normalized]
    while queue:
        current = queue.pop(0)
        if not current or current in resolved:
            continue
        if not _SAFE_ID_RE.match(current):
            continue
        resolved.add(current)
        for suffix in ("", "_reverse"):
            mapping_path = session_dir / f"lid-mapping-{current}{suffix}.json"
            if not mapping_path.exists():
                continue
            try:
                mapped = normalize_whatsapp_id(
                    json.loads(mapping_path.read_text(encoding="utf-8"))
                )
            except (OSError, json.JSONDecodeError):
                continue
            if mapped and mapped not in resolved:
                queue.append(mapped)
    return resolved


class Allowlist:
    def __init__(self, allowed: list, session_dir: Path = None):
        self._set = {u.strip() for u in (allowed or []) if u.strip()}
        self._bare = {u.lstrip("+") for u in self._set if u.startswith("+")}
        self._session_dir = session_dir

    def is_allowed(self, user_id: str) -> bool:
        if "*" in self._set:
            return True
        if user_id in self._set:
            return True
        bare = user_id.lstrip("+")
        if bare in self._bare:
            return True
        if self._session_dir and self._session_dir.exists():
            aliases = expand_whatsapp_aliases(user_id, self._session_dir)
            for alias in aliases:
                if alias in self._set or alias.lstrip("+") in self._bare:
                    return True
        return False

    def add(self, user_id: str) -> None:
        self._set.add(user_id)

    def remove(self, user_id: str) -> None:
        self._set.discard(user_id)

    def __contains__(self, user_id) -> bool:
        return self.is_allowed(user_id)

    def __len__(self) -> int:
        return len(self._set)

    def __iter__(self):
        return iter(sorted(self._set))

    @classmethod
    def from_config(cls, config: dict) -> "Allowlist":
        users = []
        for key in ("whatsapp_allowed_users", "slack_allowed_users", "signal_allowed_users"):
            raw = config.get(key, "") or ""
            users.extend(u.strip() for u in raw.split(",") if u.strip())
        session_dir = None
        whatsapp_session = config.get("whatsapp_session_dir", "") or ""
        if whatsapp_session:
            session_dir = Path(whatsapp_session).expanduser()
        return cls(users, session_dir=session_dir)