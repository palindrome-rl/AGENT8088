"""The banner must not depend on the machine's locale codec.

`Path.read_text()` with no encoding uses the locale default. On Windows that is
cp1252, which cannot decode the UTF-8 ANSI logo — so `banner()` raised
UnicodeDecodeError before the REPL appeared. It worked on Linux only because the
default there is UTF-8.
"""
import io
import locale

from rich.console import Console

import agent8088.cli as classic


def test_the_ansi_logo_asset_is_utf8():
    if not classic._PALINDROME_ANSI_LOGO.is_file():
        return  # asset not present in this layout; the fallback path covers it
    raw = classic._PALINDROME_ANSI_LOGO.read_bytes()
    raw.decode("utf-8")  # raises if the asset stops being UTF-8


def test_the_logo_loads_under_a_non_utf8_locale(monkeypatch):
    """Simulates the Windows default without needing a Windows runner."""
    monkeypatch.setattr(locale, "getpreferredencoding", lambda *_a, **_k: "cp1252")
    logo = classic._palindrome_logo()
    assert logo.plain.strip(), "the logo must render, not raise"


def test_banner_renders_without_a_decode_error(monkeypatch):
    """The regression was a crash at startup, not a cosmetic difference."""
    output = io.StringIO()
    monkeypatch.setattr(classic, "console",
                        Console(file=output, width=120, color_system=None,
                                legacy_windows=False))

    classic.banner()   # raised UnicodeDecodeError on a cp1252 machine

    assert "Palindrome" in output.getvalue()
