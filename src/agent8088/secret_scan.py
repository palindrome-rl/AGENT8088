"""Flag credential-shaped strings in user input and in files a run wrote.

The vendor-prefix table and the opaque-value shape test follow
NousResearch/hermes-agent (``agent/redact.py``), which keeps bare
hash-and-blob detection to six lines: a long hex string or a long
base64-ish token is flagged whatever it is called, because at the point
it is pasted or written nobody can tell a commit SHA from a password hash
from an API key.

What this is NOT: it does not know which strings are *real* secrets. It
only reports shapes. A false positive costs the user one glance; a false
negative ships a live credential, so the bias here is deliberately
towards reporting.

Deliberately absent: generic ``password = "hunter2"`` rules. They match
half of all config files, and a check that cries wolf every run is a
check everyone learns to ignore.
"""

import re
from pathlib import Path

# Shapes that can only be a credential. Anchored on a literal vendor
# prefix, so precision is high and the length floor does the rest.
_PREFIX_PATTERNS = (
    (r"sk-[A-Za-z0-9_-]{16,}", "API key (sk-)"),
    (r"sk_live_[A-Za-z0-9]{10,}", "Stripe secret key"),
    (r"sk_test_[A-Za-z0-9]{10,}", "Stripe test key"),
    (r"rk_live_[A-Za-z0-9]{10,}", "Stripe restricted key"),
    (r"sk_[A-Za-z0-9_]{16,}", "ElevenLabs / service key"),
    (r"ghp_[A-Za-z0-9]{20,}", "GitHub PAT"),
    (r"github_pat_[A-Za-z0-9_]{20,}", "GitHub fine-grained PAT"),
    (r"gho_[A-Za-z0-9]{20,}", "GitHub OAuth token"),
    (r"ghs_[A-Za-z0-9]{20,}", "GitHub server token"),
    (r"ghu_[A-Za-z0-9]{20,}", "GitHub user token"),
    (r"ghr_[A-Za-z0-9]{20,}", "GitHub refresh token"),
    (r"glpat-[A-Za-z0-9_-]{16,}", "GitLab PAT"),
    (r"gldt-[A-Za-z0-9_-]{16,}", "GitLab deploy token"),
    (r"AKIA[A-Z0-9]{16}", "AWS access key id"),
    (r"ASIA[A-Z0-9]{16}", "AWS temporary access key id"),
    (r"AIza[A-Za-z0-9_-]{30,}", "Google API key"),
    (r"xox[baprs]-[A-Za-z0-9-]{10,}", "Slack token"),
    (r"xapp-\d-[A-Za-z0-9-]{10,}", "Slack app-level token"),
    (r"hf_[A-Za-z0-9]{30,}", "HuggingFace token"),
    (r"npm_[A-Za-z0-9]{30,}", "npm token"),
    (r"pypi-[A-Za-z0-9_-]{20,}", "PyPI token"),
    (r"dop_v1_[A-Za-z0-9]{30,}", "DigitalOcean token"),
    (r"SG\.[A-Za-z0-9_-]{20,}", "SendGrid API key"),
    (r"tvly-[A-Za-z0-9]{20,}", "Tavily API key"),
    (r"exa_[A-Za-z0-9]{20,}", "Exa API key"),
    (r"gsk_[A-Za-z0-9]{20,}", "Groq API key"),
    (r"xai-[A-Za-z0-9]{40,}", "xAI API key"),
    (r"ntn_[A-Za-z0-9]{30,}", "Notion integration token"),
    (r"r8_[A-Za-z0-9]{30,}", "Replicate API token"),
    (r"eyJ[A-Za-z0-9_-]{10,}(?:\.[A-Za-z0-9_=-]{4,}){2}", "JSON web token"),
    (r"-----BEGIN[A-Z ]*PRIVATE KEY-----", "Private key"),
    (r"(?:mongodb|postgres(?:ql)?|mysql|redis|amqp)://[^\s:@/]+:[^\s@/]{6,}@",
     "Database connection string with a password"),
)

_COMPILED = tuple((re.compile(p), label) for p, label in _PREFIX_PATTERNS)

# A bare blob with no key name to judge it by. Six lines, from Hermes.
_HEX_RE = re.compile(r"[A-Fa-f0-9]+")
_BLOB_RE = re.compile(r"[A-Za-z0-9_./+=-]+")
_MIXED = (r"[a-z]", r"[A-Z]", r"[0-9]")

_MIN_HEX = 16
_MIN_BLOB = 20
_MIN_MIXED = 12

# A run of digits is never a credential, however long: timestamps, ids and
# counters all look like that. Same reasoning as detect-secrets' digit
# penalty, applied as a plain exclusion.
_DIGITS_ONLY_RE = re.compile(r"[0-9]+")

# Never reported: these are shapes that contain long opaque runs by
# design and are public by nature.
_URL_RE = re.compile(r"[a-z][a-z0-9+.-]*://", re.IGNORECASE)
_PATHY_RE = re.compile(r"^(?:\.{0,2}/|~/)|[A-Za-z0-9_-]\.[a-z]{1,5}$")
# A product and its version, the shape of every User-Agent part: HarborWatch/1.0, Chrome/118.0.0.0,
# Python-urllib/3.11. The name is words, so a random-looking key with a trailing /5.0 does not match.
_PRODUCT_VERSION_RE = re.compile(
    r"[A-Za-z][A-Za-z_-]{2,}[0-9]{0,2}/v?[0-9]+(?:\.[0-9]+){0,3}(?:[-+][\w.]+)?")
# Plain words joined by slashes, plus signs, dashes or dots: "moves/items/partners", "application/xhtml+xml",
# "atkMult/defMult". No digit, and every part is letters with at most two capitals (a word, a Capitalised
# word or lowerCamel). A random key has digits, or about half its letters in capitals.
_JOINER_RE = re.compile(r"[/+_.\-]")
# A URL up to the next space or closing quote or bracket; judged by the URL rules, not as a pile of tokens.
_URL_TEXT_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^\s'\"<>)\]]+")
OPAQUE_KIND = "Possible credential (opaque string)"
_HASHISH_WORDS = frozenset({
    "sha256", "sha-256", "sha1", "md5", "sha512", "checksum", "digest",
})
_TOKEN_SPLIT_RE = re.compile(r"[^\w./+=:@-]+")
_EDGE_PUNCT = "\"'`()[]{}<>,;!?*"


class Finding:
    """One credential-shaped string. ``masked`` is safe to show or log."""

    __slots__ = ("kind", "masked", "weak")

    def __init__(self, kind, masked, weak=False):
        self.kind = kind
        self.masked = masked
        # Found only because of how the string looks and with no digit in it, so it can only have come through
        # the slash / plus / equals route of the opaque rule: worth one question, not a standing demand.
        self.weak = weak

    def __repr__(self):
        return "Finding(%r, %r)" % (self.kind, self.masked)


def mask(value: str) -> str:
    """First four, last four. Short values are starred out whole.

    A masked secret still has to be useless on its own, so anything under
    twelve characters is not partially revealed.
    """
    value = value.strip()
    if len(value) < 12:
        return "*" * len(value)
    return value[:4] + "..." + value[-4:]


def looks_like_opaque_credential(value: str) -> bool:
    """Credential-like shape for a value with no key name to judge it by.

    Short technical scalars (``CPU``, ``local``, ``max_tokens``) must not
    be reported merely for looking odd, so the bar rises with length.
    The longest rule asks for all three character classes, not two: a
    mixed-case blob with no digit in it is far more often base64 of a
    word than a credential.
    """
    if _DIGITS_ONLY_RE.fullmatch(value):
        return False
    if len(value) >= _MIN_HEX and _HEX_RE.fullmatch(value):
        return True
    # A long run of letters alone is an identifier (BaseHTTPRequestHandler), not a
    # key: every real blob of this length carries a digit or base64 punctuation.
    if (len(value) >= _MIN_BLOB and _BLOB_RE.fullmatch(value)
            and re.search(r"[0-9+/=]", value)):
        return True
    if len(value) < _MIN_MIXED:
        return False
    return all(re.search(p, value) for p in _MIXED)


def _wordlike(token: str) -> bool:
    """Words joined by separators, with no digit: text, not a key."""
    if re.search(r"[0-9]", token):
        return False
    parts = [p for p in _JOINER_RE.split(token) if p]
    return bool(parts) and all(p.isalpha() and sum(c.isupper() for c in p) <= 2 for p in parts)


_CODE_SPLIT_RE = re.compile(r"[:=+/.$-]")
_SHORT_NUMBER_RE = re.compile(r"[0-9]{1,6}(?:\.[0-9]{1,4})?")


def _code_piece(piece: str) -> bool:
    """One identifier-sized word: letters with at most two digits and four capitals (``effectChance``,
    ``f2b``, ``createElement``), or ALL CAPS."""
    if not piece or len(piece) > 24 or not re.fullmatch(r"[A-Za-z_]\w*", piece):
        return False
    if sum(c.isdigit() for c in piece) > 2:
        return False
    return piece.isupper() or sum(c.isupper() for c in piece) <= 4


def _codelike(token: str) -> bool:
    """Source code that happens to be one token: ``effectChance:30``, ``d=document.createElement``,
    ``a.b.c``. Every part between the separators is a short identifier or a short number, so there is no
    run long enough or random enough to be a key (a random 20 character part has far more than two digits
    or four capitals)."""
    if not _CODE_SPLIT_RE.search(token):
        return False
    pieces = [p for p in _CODE_SPLIT_RE.split(token) if p]
    return bool(pieces) and all(_SHORT_NUMBER_RE.fullmatch(p) or _code_piece(p) for p in pieces)


def _is_noise(token: str) -> bool:
    if _URL_RE.search(token):
        return True
    if _wordlike(token) or _codelike(token):
        return True
    if _PATHY_RE.search(token):
        return True
    if _PRODUCT_VERSION_RE.fullmatch(token):
        return True
    if token.lower() in _HASHISH_WORDS:
        return True
    # A dotted word is a filename or a package, not a credential.
    if token.count(".") == 1 and " " not in token and token.split(".")[0].isalpha():
        return True
    return False


def find_secrets(text: str) -> list:
    """Credential-shaped strings in ``text``. Empty list when there are none."""
    if not text or not text.strip():
        return []
    found = []
    seen = set()

    for pattern, label in _COMPILED:
        for match in pattern.finditer(text):
            value = match.group(0)
            if value in seen:
                continue
            seen.add(value)
            found.append(Finding(label, mask(value)))

    # The opaque-string pass judges what is left once URLs are taken out: a percent-encoded address would
    # otherwise be cut at every "%" into pieces that no longer look like part of a URL.
    for raw in _TOKEN_SPLIT_RE.split(_URL_TEXT_RE.sub(" ", text)):
        token = raw.strip(_EDGE_PUNCT)
        if len(token) < _MIN_MIXED or token in seen or _is_noise(token):
            continue
        if looks_like_opaque_credential(token):
            seen.add(token)
            found.append(Finding(OPAQUE_KIND, mask(token), weak=not re.search(r"[0-9]", token)))
    return found


# Files big enough to be generated data rather than hand-written source.
_MAX_SCAN_BYTES = 2 * 1024 * 1024
_SKIP_SUFFIXES = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".gz",
    ".bz2", ".xz", ".tar", ".7z", ".woff", ".woff2", ".ttf", ".eot", ".mp4",
    ".mp3", ".wav", ".so", ".dll", ".dylib", ".exe", ".pyc", ".class", ".jar",
    ".wasm", ".bin", ".db", ".sqlite", ".sqlite3", ".parquet", ".arrow",
})


def scan_files(paths) -> list:
    """Findings in files a run wrote, as ``(path, line_number, Finding)``."""
    hits = []
    for path in paths:
        try:
            p = Path(path)
            if not p.is_file() or p.suffix.lower() in _SKIP_SUFFIXES:
                continue
            if p.stat().st_size > _MAX_SCAN_BYTES:
                continue
            content = p.read_text(encoding="utf-8", errors="strict")
        except (OSError, UnicodeDecodeError, ValueError):
            # Unreadable, binary, or a dangling path. A scan that raises
            # would take the run down over a file it cannot open.
            continue
        for number, line in enumerate(content.splitlines(), 1):
            for finding in find_secrets(line):
                hits.append((str(p), number, finding))
    return hits


def describe(findings) -> str:
    """One line naming the shapes found. Never quotes a secret."""
    kinds = []
    for finding in findings:
        if finding.kind not in kinds:
            kinds.append(finding.kind)
    return ", ".join(kinds)


def warning(findings, where: str = "") -> str:
    """A user-facing warning. Names shapes and masked values, never secrets."""
    if not findings:
        return ""
    shown = ", ".join(f.masked for f in findings[:3])
    more = f" (+{len(findings) - 3} more)" if len(findings) > 3 else ""
    prefix = f"{where}: " if where else ""
    return (f"{prefix}this looks like it contains a credential "
            f"({describe(findings)}): {shown}{more}. "
            f"It is now in this session's history and on the model's context. "
            f"If it is a live key, revoke and reissue it.")