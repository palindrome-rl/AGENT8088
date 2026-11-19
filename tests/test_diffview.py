import difflib

from agent8088 import diffview


def _diff(old: str, new: str, name: str = "app.py") -> list[str]:
    return list(difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile=f"{name} (old)", tofile=name, lineterm="",
    ))


def test_parse_hunks_tracks_both_line_numbers():
    lines = _diff("a\nb\nc\n", "a\nB\nc\n")
    hunks = diffview.parse_hunks(lines)
    assert len(hunks) == 1
    rows = hunks[0]["rows"]
    assert ("-", 2, None, "b") in rows
    assert ("+", None, 2, "B") in rows


def test_diff_path_reads_the_plus_header():
    assert diffview.diff_path(_diff("a\n", "b\n", "src/x.py")) == "src/x.py"


def test_diff_path_ignores_added_lines_shaped_like_a_header():
    # An added line reading '++ tally' arrives as '+++ tally' inside the hunk.
    lines = _diff("a\n", "a\n++ tally\n", "src/x.py")
    assert diffview.diff_path(lines) == "src/x.py"


def test_diff_counts_excludes_the_preamble():
    assert diffview.diff_counts(_diff("a\nb\n", "a\nB\n")) == (1, 1)


def test_to_payload_shape():
    payload = diffview.to_payload(_diff("a\nb\nc\n", "a\nB\nc\n", "src/x.py"))
    assert payload["file"] == "src/x.py"
    assert (payload["added"], payload["removed"]) == (1, 1)
    assert {"text": "B", "tone": "add"} in payload["lines"]
    assert {"text": "b", "tone": "del"} in payload["lines"]
    assert {"text": "a", "tone": "ctx"} in payload["lines"]
    assert payload["truncated"] is False


def test_to_payload_is_none_for_an_empty_diff():
    assert diffview.to_payload([]) is None


def test_to_payload_truncates_and_says_so():
    # Every line changes, so the diff is long enough to actually hit the cap.
    old = "".join(f"line {i}\n" for i in range(500))
    new = "".join(f"LINE {i}\n" for i in range(500))
    payload = diffview.to_payload(_diff(old, new), max_lines=5)
    assert len(payload["lines"]) == 5
    assert payload["truncated"] is True
