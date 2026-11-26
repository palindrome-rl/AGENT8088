"""Turn a title into a URL slug."""
import re
import unicodedata


def slugify(title: str, max_length: int = 60) -> str:
    """Lower-case ASCII words joined by hyphens.

    Accents are dropped, not the letters under them."""
    # NFKD splits "è" into "e" and a combining accent, so dropping non-ASCII keeps the "e".
    text = unicodedata.normalize("NFKD", title)
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    if len(text) > max_length:
        text = text[:max_length].rsplit("-", 1)[0]
    return text
