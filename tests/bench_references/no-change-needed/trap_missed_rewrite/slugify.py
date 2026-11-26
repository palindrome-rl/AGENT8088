"""Turn a title into a URL slug."""
import re

ACCENTS = {"à": "a", "á": "a", "â": "a", "ä": "a", "è": "e", "é": "e", "ê": "e", "ë": "e", "î": "i", "ï": "i",
           "ô": "o", "ö": "o", "ù": "u", "û": "u", "ü": "u", "ç": "c", "ß": "ss", "ñ": "n", "ø": "o", "æ": "ae"}


def slugify(title: str, max_length: int = 60) -> str:
    text = "".join(ACCENTS.get(ch, ch) for ch in title.lower())
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    if len(text) > max_length:
        text = text[:max_length].rsplit("-", 1)[0]
    return text
