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
    """Who may talk to the gateway.

    Ids are scoped per platform when they come from config: an id listed under
    `slack_allowed_users` is valid on Slack only, not on Discord or WhatsApp.
    Ids added without a platform (the `Allowlist([...])` constructor, `.add()`)
    are global, and `is_allowed()` called without a platform falls back to the
    union — so existing callers keep working.
    """

    def __init__(self, allowed: list, session_dir: Path = None, by_platform: dict = None):
        self._set = {u.strip() for u in (allowed or []) if u.strip()}
        self._bare = {u.lstrip("+") for u in self._set if u.startswith("+")}
        self._session_dir = session_dir
        # platform -> set of ids scoped to it. Ids in self._set that are not in
        # any platform bucket are global.
        self._by_platform = {p: set(ids) for p, ids in (by_platform or {}).items()}

    def _candidates(self, platform: str = None) -> set:
        """Ids valid for this platform: its own scoped ids plus global ones."""
        if platform is None or not self._by_platform:
            return self._set
        scoped = set().union(*self._by_platform.values()) if self._by_platform else set()
        globals_ = self._set - scoped
        return self._by_platform.get(platform, set()) | globals_

    def is_allowed(self, user_id: str, platform: str = None) -> bool:
        allowed = self._candidates(platform)
        if "*" in allowed:
            return True
        if user_id in allowed:
            return True
        bare_allowed = {u.lstrip("+") for u in allowed if u.startswith("+")}
        bare = user_id.lstrip("+")
        if bare in bare_allowed:
            return True
        if self._session_dir and self._session_dir.exists():
            aliases = expand_whatsapp_aliases(user_id, self._session_dir)
            for alias in aliases:
                if alias in allowed or alias.lstrip("+") in bare_allowed:
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
        by_platform = {}
        for key in ("whatsapp_allowed_users", "slack_allowed_users", "signal_allowed_users", "discord_allowed_users"):
            raw = config.get(key, "") or ""
            entries = [u.strip() for u in raw.split(",") if u.strip()]
            if not entries:
                continue
            users.extend(entries)
            by_platform.setdefault(key.split("_", 1)[0], set()).update(entries)
        session_dir = None
        whatsapp_session = config.get("whatsapp_session_dir", "") or ""
        if whatsapp_session:
            session_dir = Path(whatsapp_session).expanduser()
        return cls(users, session_dir=session_dir, by_platform=by_platform)