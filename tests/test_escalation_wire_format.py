"""One delimiter, everywhere.

The escalation payload is `\\x1f`-delimited because a Windows path (`C:\\Users\\...`)
splits on ':' and corrupts the parse. What makes a missed call site dangerous is
that it does not raise: `startswith` simply stops matching, the caller takes the
not-an-escalation branch, and a gated action either reads as completed output or
stops prompting the user at all.

Three of these had already gone unnoticed on this branch — two failing loudly and
one, a negative assertion, passing for free and therefore no longer testing
anything. The invariant worth pinning is structural ("no straggler exists"), which
no single behavioural test can assert, so one test here reads the source.

The predicate is `"ESCALATION_REQUEST:` — *with* the opening quote — not the bare
token. Both modules carry prose about the old format in comments and docstrings
(``... like `Error:` or `ESCALATION_REQUEST:` is no longer ...``). Matching the
bare token flags that prose forever, which makes the test unpassable and then
ignored, which is worse than not having it.
"""
import pathlib

import pytest

from agent8088 import cli
from agent8088 import engine as engine_module

PREFIX = "ESCALATION_REQUEST\x1f"


def _stale_lines(module):
    source = pathlib.Path(module.__file__).read_text(encoding="utf-8")
    return [f"{n}: {line.strip()}" for n, line in enumerate(source.splitlines(), 1)
            if '"ESCALATION_REQUEST:' in line and not line.strip().startswith("#")]


@pytest.mark.parametrize("module", [engine_module, cli],
                         ids=["engine", "cli"])
def test_no_call_site_compares_against_the_old_prefix(module):
    assert _stale_lines(module) == []


def _stale_in_tree(root, pattern):
    this_file = pathlib.Path(__file__).resolve()
    stale = []
    for path in sorted(root.rglob(pattern)):
        if path.resolve() == this_file:
            continue   # defines the predicate and the deliberate negative case
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if '"ESCALATION_REQUEST:' in line and not line.strip().startswith("#"):
                stale.append(f"{path.name}:{n}")
    return stale


def test_the_test_suite_itself_has_no_stale_assertions():
    """An assertion against the old prefix is worse than a stale call site: a
    positive one fails for a reason that looks unrelated, and a negative one
    passes for free and silently stops covering what it names. This branch had
    one of each."""
    assert _stale_in_tree(pathlib.Path(__file__).parent, "test_*.py") == []


def test_the_verification_scripts_have_no_stale_assertions():
    """`scripts/` is where two of these hid. Nothing there is collected by pytest,
    so the only signal was verify_features.py reporting failures with a mangled
    `ESCALATION_REQUESTeditlocal_execution` in the message — the separators are
    non-printing, so it reads as a typo rather than a wire-format mismatch."""
    scripts = pathlib.Path(__file__).resolve().parent.parent / "scripts"
    assert scripts.is_dir(), f"expected {scripts} to exist"
    assert _stale_in_tree(scripts, "*.py") == []


def test_the_wire_format_is_unit_separated(engine):
    payload = engine.request_escalation("edit", ["/tmp/x.txt"], "new_file", "why")

    assert payload.startswith(PREFIX)
    assert payload.split("\x1f", 4) == ["ESCALATION_REQUEST", "edit", "new_file",
                                        "/tmp/x.txt", "why"]


def test_a_windows_path_survives_the_round_trip(engine):
    """The reason the delimiter changed at all. On ':' this split into the wrong
    number of fields and the drive letter became a field of its own."""
    path = r"C:\Users\Admin\My Documents\out.txt"
    payload = engine.request_escalation("edit", [path], "new_file", "needs write access")

    parts = payload.split("\x1f", 4)

    assert len(parts) == 5
    assert parts[3] == path, "the path must arrive byte-identical"
    assert parts[4] == "needs write access"


def test_the_cli_parses_what_the_engine_emits(engine, monkeypatch):
    """Producer and consumer agree. Asserting the format on one side only is how
    the two drifted apart in the first place."""
    monkeypatch.setattr(cli.console, "input", lambda *a, **k: "d")
    payload = engine.request_escalation("edit", [r"C:\tmp\a b.txt"], "new_file", "why")

    assert cli._handle_escalation(payload) is False, "declined, but parsed"


def test_a_colon_delimited_payload_is_not_treated_as_an_escalation(monkeypatch):
    """Belt and braces: the old format must not parse, so a half-converted call
    site fails in a test rather than quietly in production."""
    monkeypatch.setattr(cli.console, "input", lambda *a, **k: "o")

    assert cli._handle_escalation(
        "ESCALATION_REQUEST:edit:new_file:/tmp/x:why") is False
