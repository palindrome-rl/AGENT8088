"""The clause splitter in the output check breaks at the words and, then and but, and no source file carries a stray
control character.

`_CLAUSE_BREAK` was written with backspace characters where the word-boundary escape belonged, so it never split at those
words and "see notes.md and write spec.json" read the second file as something to look at, not something to produce."""
from pathlib import Path

import pytest

from agent8088 import completion as c


@pytest.mark.parametrize("request_text", [
    "Read the layout (see notes.md) and write spec.json.",
    "See notes.md and write spec.json.",
    "Look at the layout in notes.md then save spec.json.",
    "The data is in notes.md but write spec.json yourself.",
])
def test_a_clause_that_asks_for_the_file_makes_it_an_output(request_text):
    assert c.is_input_reference(request_text, "spec.json") is False


@pytest.mark.parametrize("request_text", [
    "Create the page and see spec.json for the fields.",
    "The sandbox layout is described in spec.json.",
    "Create a handbook page; the fields are given in spec.json.",
])
def test_words_that_only_contain_the_connectives_do_not_split_a_clause(request_text):
    assert c.is_input_reference(request_text, "spec.json") is True


def test_the_splitter_cuts_at_whole_words_only():
    assert c._CLAUSE_BREAK.split("one and two then three but four") == ["one ", " two ", " three ", " four"]
    assert c._CLAUSE_BREAK.split("sandbox thence butter") == ["sandbox thence butter"]


def test_no_source_file_has_a_stray_control_character():
    """A `\\b` written in a non-raw string, or pasted through a tool, becomes a backspace and silently changes a regex."""
    root = Path(c.__file__).resolve().parent
    allowed = {"\t", "\n", "\r", "\x0c"}
    bad = []
    for path in sorted(root.rglob("*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        for number, line in enumerate(text.splitlines(), 1):
            if any(ord(ch) < 32 and ch not in allowed for ch in line):
                bad.append(f"{path.relative_to(root)}:{number}")
    assert not bad, bad
