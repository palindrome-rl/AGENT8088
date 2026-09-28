import json
import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

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

    A misplaced id is denied by default. Set `strict_platform_allowlist=0`
    only as a temporary migration aid; it allows the misplaced id with a
    one-time warning that names the correct configuration line.
    """

    def __init__(self, allowed: list, session_dir: Path = None, by_platform: dict = None,
                 strict: bool = True):
        self._set = {u.strip() for u in (allowed or []) if u.strip()}
        self._bare = {u.lstrip("+") for u in self._set if u.startswith("+")}
        self._session_dir = session_dir
        # platform -> set of ids scoped to it. Ids in self._set that are not in
        # any platform bucket are global.
        self._by_platform = {p: set(ids) for p, ids in (by_platform or {}).items()}
        self.strict = bool(strict)
        self._warned_misplaced = set()

    def _candidates(self, platform: str = None) -> set:
        """Ids valid for this platform: its own scoped ids plus global ones."""
        if platform is None or not self._by_platform:
            return self._set
        scoped = set().union(*self._by_platform.values()) if self._by_platform else set()
        globals_ = self._set - scoped
        return self._by_platform.get(platform, set()) | globals_

    def _matches(self, user_id: str, allowed: set) -> bool:
        """True if user_id is covered by this set of allowed ids."""
        if "*" in allowed or user_id in allowed:
            return True
        bare_allowed = {u.lstrip("+") for u in allowed if u.startswith("+")}
        if user_id.lstrip("+") in bare_allowed:
            return True
        if self._session_dir and self._session_dir.exists():
            for alias in expand_whatsapp_aliases(user_id, self._session_dir):
                if alias in allowed or alias.lstrip("+") in bare_allowed:
                    return True
        return False

    def _listed_under(self, user_id: str, platform: str) -> str:
        """Name of another platform whose list covers user_id, or ""."""
        for other, ids in self._by_platform.items():
            if other != platform and self._matches(user_id, ids):
                return other
        return ""

    def is_allowed(self, user_id: str, platform: str = None) -> bool:
        if self._matches(user_id, self._candidates(platform)):
            return True
        if platform is None:
            return False
        # Listed, but on another platform's line. Deny by default: platform
        # identities are an authorization boundary, not a migration hint.
        other = self._listed_under(user_id, platform)
        if not other:
            return False
        if self.strict:
            log.warning(
                "denied %s on %s: it is listed under %s_allowed_users, not "
                "%s_allowed_users (strict_platform_allowlist is on)",
                user_id, platform, other, platform,
            )
            return False
        if user_id not in self._warned_misplaced:
            self._warned_misplaced.add(user_id)
            log.warning(
                "allowing %s on %s, but it is configured under "
                "%s_allowed_users — move it to %s_allowed_users, then remove "
                "strict_platform_allowlist=0.",
                user_id, platform, other, platform,
            )
        return True

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
        for key in ("whatsapp_allowed_users", "slack_allowed_users", "signal_allowed_users", "discord_allowed_users", "email_allowed_users", "telegram_allowed_users"):
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
        strict = str(config.get("strict_platform_allowlist", "1")).strip().lower() in (
            "1", "true", "yes", "on")
        return cls(users, session_dir=session_dir, by_platform=by_platform, strict=strict)
