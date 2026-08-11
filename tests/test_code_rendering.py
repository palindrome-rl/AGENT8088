"""Code rendering: listings and diffs must look like an editor, not a data dump.

Two things were wrong with how the CLI showed the code it generates. The diff
renderer appended a newline to lines difflib had already terminated
(`keepends=True`), so every diff printed double-spaced. And nothing was ever
syntax-highlighted, so a written file arrived as an undifferentiated wall of
monospace and a brand-new file arrived as a hundred identical '+' rows.

These tests pin the fixed shape: one row per source line, real line numbers,
conventional add/remove colours, per-file lexing — and, above all, that the
source survives the styling byte for byte, since the trace is the user's only
view of what was written to disk.

Pure string/renderable work: no files, no config, no engine calls.
"""
import io

import pytest
from rich.console import Console
from rich.text import Text

import agent8088.cli as cli

PY = '''def add_book(data: dict, title: str) -> dict:
    """Add a new book.

    Raises ValueError if the ISBN already exists.
    """
    book = {"title": title, "status": "available"}
    data["books"].append(book)
    return book
'''


def render(renderable, width=100):
    """The renderable as an ANSI string, exactly as a colour terminal would get it."""
    console = Console(file=io.StringIO(), width=width, force_terminal=True,
                      color_system="truecolor", legacy_windows=False)
    console.print(renderable)
    return console.file.getvalue()


def rows(renderable, width=100):
    """Visible rows, styles stripped and trailing padding removed."""
    console = Console(file=io.StringIO(), width=width, force_terminal=False,
                      legacy_windows=False)
    console.print(renderable)
    return [line.rstrip() for line in console.file.getvalue().rstrip("\n").split("\n")]


def colours(ansi_row):
    """The foreground colours used in one rendered row, in order."""
    return [part.split("m")[0] for part in ansi_row.split("\x1b[")
            if part.startswith("38;2;")]


# Line numbers are right-aligned to at least two columns and followed by two
# spaces, so a listing of under 100 lines has a four-character gutter.
GUTTER = 4


def diff_of(old, new, filename="library.py"):
    """A unified diff in exactly the shape engine._make_diff hands to the CLI."""
    import difflib
    return list(difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile=f"{filename} (old)", tofile=filename, lineterm="",
    ))


@pytest.fixture(autouse=True)
def _known_theme(monkeypatch):
    """Pin the theme so colour assertions do not depend on a developer's config."""
    monkeypatch.setitem(cli.A.APP_CONFIG, "syntax_theme", "monokai")


@pytest.fixture
def no_highlight(monkeypatch):
    monkeypatch.setitem(cli.A.APP_CONFIG, "syntax_theme", "none")


# ---------------------------------------------------------------------------
# The regression: difflib's keepends newline was being doubled
# ---------------------------------------------------------------------------
def test_diff_prints_one_row_per_diff_line():
    """The bug: `keepends=True` lines already end in '\\n' and the renderer added
    another, so a 60-line diff occupied 120 rows of blank-separated output."""
    diff = diff_of("a = 1\nb = 2\nc = 3\n", "a = 1\nb = 99\nc = 3\n")
    body = [row for row in rows(cli._diff_block(diff, path="x.py")) if row]

    assert len(body) == len([line for line in diff if not line.startswith(("---", "+++", "@@"))])
    assert "" not in rows(cli._diff_block(diff, path="x.py"))[:len(body)]


def test_diff_code_keeps_no_stray_newline():
    diff = diff_of("a = 1\n", "a = 2\n")
    plain = render(cli._diff_block(diff, path="x.py"))
    assert "\n\n" not in plain.strip()


# ---------------------------------------------------------------------------
# Editor shape: line numbers, markers, truncation
# ---------------------------------------------------------------------------
def test_listing_numbers_every_line_and_keeps_the_source_intact():
    body, total = cli._numbered_lines(PY, path="library.py")
    assert total == 8
    visible = [row for row in rows(body) if row]
    for number, source in enumerate(PY.rstrip("\n").split("\n"), 1):
        assert visible[number - 1].endswith(source.rstrip())
        assert visible[number - 1].lstrip().startswith(str(number))


def test_listing_truncation_note_counts_the_hidden_lines():
    body, total = cli._numbered_lines(PY, limit=3, path="library.py")
    assert total == 8
    assert "5 more lines" in "\n".join(rows(body))


def test_diff_line_numbers_come_from_the_hunk_header():
    """A diff against line 40 of a file must say 40, not 1 — the numbers are the
    only way to tell where in the file the change landed."""
    old = "".join(f"line {n}\n" for n in range(1, 61))
    new = old.replace("line 42\n", "line 42 changed\n")
    body = "\n".join(rows(cli._diff_block(diff_of(old, new), path="x.txt")))

    assert "42 - line 42" in body
    assert "42 + line 42 changed" in body


def test_diff_marks_additions_and_removals():
    diff = diff_of("keep = 1\ndrop = 2\n", "keep = 1\nadd = 3\n")
    body = "\n".join(rows(cli._diff_block(diff, path="x.py")))
    assert "- drop = 2" in body
    assert "+ add = 3" in body


def test_diff_truncates_with_a_count():
    old = "".join(f"line {n}\n" for n in range(1, 40))
    body = "\n".join(rows(cli._diff_block(diff_of(old, ""), limit=5, path="x.txt")))
    assert "more diff lines" in body


# ---------------------------------------------------------------------------
# A new file is a listing, not a wall of '+'
# ---------------------------------------------------------------------------
def test_new_file_renders_as_a_numbered_listing():
    """`@@ -0,0 +1,N @@` means every row is an addition; 'this line is new' is
    already said by the header, so the rows should read as the file itself."""
    body = rows(cli._diff_block(diff_of("", PY), path="library.py"))
    visible = [row for row in body if row]

    assert not any(row.lstrip().startswith("+") for row in visible)
    assert visible[0].endswith("def add_book(data: dict, title: str) -> dict:")
    assert visible[0].lstrip().startswith("1")


def test_edit_to_an_existing_file_still_renders_as_a_diff():
    body = "\n".join(rows(cli._diff_block(diff_of("a = 1\n", "a = 2\n"), path="x.py")))
    assert "- a = 1" in body
    assert "+ a = 2" in body


# ---------------------------------------------------------------------------
# Syntax highlighting
# ---------------------------------------------------------------------------
def test_python_listing_is_syntax_highlighted():
    ansi = render(cli._numbered_lines(PY, path="library.py")[0])
    # More than one foreground colour means the lexer actually ran; a keyword and
    # a string literal cannot share a colour in any usable theme.
    assert len(set(colours(ansi))) > 1


def test_highlighting_is_chosen_per_file_extension():
    code = "SELECT title FROM books WHERE status = 'available';\n"
    as_sql = render(cli._numbered_lines(code, path="q.sql")[0])
    as_text = render(cli._numbered_lines(code, path="q.unknownext")[0])
    assert as_sql != as_text


def test_unknown_extension_still_renders_the_source():
    body, total = cli._numbered_lines("hello\nworld\n", path="notes.qqqzzz")
    assert total == 2
    assert [row for row in rows(body) if row][0].endswith("hello")


def test_diff_bodies_are_highlighted_too():
    diff = diff_of("x = 1\n", 'x = "two"\n')
    assert colours(render(cli._diff_block(diff, path="x.py")))


def test_syntax_theme_none_disables_highlighting(no_highlight):
    body, _ = cli._numbered_lines(PY, path="library.py")
    assert not colours(render(body))
    assert rows(body)[0].endswith("def add_book(data: dict, title: str) -> dict:")


def test_a_broken_theme_name_falls_back_to_the_default_theme(monkeypatch):
    """Rich substitutes Pygments' light-background default for an unknown style,
    which on a dark terminal renders code as near-black on near-black. A typo in
    `syntax_theme` has to land on the documented default instead."""
    monkeypatch.setitem(cli.A.APP_CONFIG, "syntax_theme", "no-such-theme-at-all")
    assert cli._syntax_theme() == cli._DEFAULT_THEME

    body, total = cli._numbered_lines(PY, path="library.py")
    assert total == 8
    assert rows(body)[0].endswith("def add_book(data: dict, title: str) -> dict:")

    monkeypatch.setitem(cli.A.APP_CONFIG, "syntax_theme", cli._DEFAULT_THEME)
    assert render(cli._numbered_lines(PY, path="library.py")[0]) == render(body)


def test_a_broken_theme_name_is_reported_once_at_startup(monkeypatch):
    printed = []
    monkeypatch.setattr(cli.console, "print", lambda *a, **kw: printed.append(str(a[0])))

    monkeypatch.setitem(cli.A.APP_CONFIG, "syntax_theme", "nord")
    cli.warn_about_unknown_theme()
    monkeypatch.setitem(cli.A.APP_CONFIG, "syntax_theme", "none")
    cli.warn_about_unknown_theme()
    assert printed == []

    monkeypatch.setitem(cli.A.APP_CONFIG, "syntax_theme", "mistyped-theme")
    cli.warn_about_unknown_theme()
    assert len(printed) == 1
    assert "mistyped-theme" in printed[0]


# ---------------------------------------------------------------------------
# Content integrity — the trace is the only view of what was written
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("code", [
    "",
    "\n",
    "one line, no trailing newline",
    "blank\n\nbetween\n",
    "trailing blank\n\n",
    "crlf\r\nlines\r\n",
    "tabs\there\n",
    "unicode — em dash, 日本語\n",
    'markup [bold]not[/bold] parsed\n',
    "{brace} and %percent%\n",
    "  leading spaces kept\n",
])
def test_listing_never_alters_the_source(code):
    """Every source line comes back, in order, byte for byte behind its number.

    Tabs are the one licensed change: Rich expands them on render, as an editor
    would. Indentation, Rich markup, braces and non-ASCII must all survive.
    """
    body, total = cli._numbered_lines(code, path="sample.py")
    # One trailing newline terminates the last line; a second one means the file
    # really does end with a blank line, and that line is part of the source.
    normalised = code.replace("\r\n", "\n").removesuffix("\n")
    expected = [line.expandtabs(4) for line in normalised.split("\n")] if normalised else []
    assert total == len(expected)

    rendered = rows(body, width=200) if expected else []
    assert len(rendered) == len(expected)
    for number, (source, row) in enumerate(zip(expected, rendered), 1):
        assert row == f"{number:>2}  {source}".rstrip()


def test_rich_markup_in_source_is_not_interpreted():
    body, _ = cli._numbered_lines("print('[red]hi[/red]')\n", path="x.py")
    assert "[red]" in "\n".join(rows(body))


def test_empty_diff_renders_nothing():
    assert "".join(rows(cli._diff_block([], path="x.py"))) == ""


def test_diff_with_multiple_hunks_shows_both():
    old = "".join(f"line {n}\n" for n in range(1, 41))
    new = old.replace("line 2\n", "line 2 edited\n").replace("line 38\n", "line 38 edited\n")
    body = "\n".join(rows(cli._diff_block(diff_of(old, new), path="x.txt")))
    assert "line 2 edited" in body
    assert "line 38 edited" in body


# ---------------------------------------------------------------------------
# Multi-line lexing: a hunk's sides are lexed as blocks, not line by line
# ---------------------------------------------------------------------------
def test_a_docstring_spanning_lines_highlights_as_one_string():
    """Line-by-line lexing restarts the lexer on every row, so the middle of a
    docstring comes out looking like ordinary code. Lexing the block as a whole is
    what keeps a continuation line the same colour as the line that opened it."""
    ansi = render(cli._numbered_lines(PY, path="library.py")[0]).split("\n")
    opening = next(row for row in ansi if "Add a new book" in row)
    continuation = next(row for row in ansi if "Raises ValueError" in row)
    assert colours(continuation)[-1] == colours(opening)[-1]


def test_the_result_hook_highlights_using_the_path_from_the_call(monkeypatch):
    """on_result is handed a tool's output but not its arguments, so the path has to
    come off the preceding on_calls. If that link breaks, every read silently loses
    its highlighting — the kind of regression nothing else would catch."""
    printed = []
    monkeypatch.setattr(cli.console, "print", lambda *args, **kw: printed.extend(args))

    cli.on_calls([{"name": "read_text", "arguments": {"filename": "sub/dir/thing.py"}}])
    printed.clear()
    cli.on_result("read_text", PY)

    assert cli._last_call_paths["read_text"] == "sub/dir/thing.py"
    assert len(set(colours(render(printed[-1])))) > 1


def test_an_edit_reports_how_many_lines_moved(monkeypatch):
    printed = []
    monkeypatch.setattr(cli.console, "print", lambda *args, **kw: printed.extend(args))
    monkeypatch.setattr(cli.A, "_last_write_diff",
                        diff_of("a = 1\nb = 2\n", "a = 1\nb = 99\nc = 3\n"), raising=False)

    cli.on_result("write_file", "Wrote 24 bytes to library.py")
    assert "+2 −1" in str(printed[0])


def test_a_new_file_reports_no_line_counts(monkeypatch):
    """Every line of a new file is an addition; '+108 −0' is noise next to a
    listing that is already numbered 1 to 108."""
    printed = []
    monkeypatch.setattr(cli.console, "print", lambda *args, **kw: printed.extend(args))
    monkeypatch.setattr(cli.A, "_last_write_diff", diff_of("", PY), raising=False)

    cli.on_result("write_file", "Wrote 240 bytes to library.py")
    assert "−" not in str(printed[0])


def test_paths_are_remembered_even_when_the_trace_is_hidden(monkeypatch):
    """/verbose off silences the trace but the next visible result still needs the
    path, so the bookkeeping has to run before the early return."""
    monkeypatch.setattr(cli.S, "verbose", "off")
    cli._last_call_paths.pop("read_text", None)
    cli.on_calls([{"name": "read_text", "arguments": {"filename": "quiet.py"}}])
    assert cli._last_call_paths["read_text"] == "quiet.py"


@pytest.mark.parametrize("comment, ext", [("-- old SQL comment", "sql"),
                                          ("-- old Haskell comment", "hs")])
def test_a_removed_comment_line_is_not_mistaken_for_a_diff_header(comment, ext):
    """Removing a line that starts with '-- ' produces '--- ...', which is exactly
    the shape of a unified diff's file header. Matching on shape alone silently
    drops the line from the diff; the header is only a header before the first
    hunk, so that is where it must be recognised."""
    diff = diff_of(f"{comment}\nkeep = 1\n", "keep = 1\n", filename=f"q.{ext}")
    body = "\n".join(rows(cli._diff_block(diff, path=f"q.{ext}")))

    assert comment in body
    assert cli._diff_counts(diff) == (0, 1)


def test_an_added_line_of_plusses_is_not_mistaken_for_a_diff_header():
    diff = diff_of("keep = 1\n", "keep = 1\n++ tally\n", filename="a.txt")
    assert "++ tally" in "\n".join(rows(cli._diff_block(diff, path="a.txt")))
    assert cli._diff_counts(diff) == (1, 0)


def test_tab_indented_code_lines_up_in_a_diff():
    """The terminal measures tab stops from the start of the row, so a tab left to
    it indents by the width of the gutter as well and a Makefile comes out ragged.
    A diff has to expand tabs against the code column, exactly as a listing does.

    The gutter is a two-column number, a space, a marker and a space, so the code
    starts at column 5 and one leading tab must become exactly _TAB_WIDTH spaces.
    """
    diff = diff_of("target:\n\techo old\n", "target:\n\techo new\n", filename="Makefile")
    body = [row for row in rows(cli._diff_block(diff, path="Makefile")) if row]
    changed = [row for row in body if "echo" in row]

    assert len(changed) == 2
    for row in changed:
        assert row[5:] == " " * cli._TAB_WIDTH + f"echo {'old' if row[3] == '-' else 'new'}"


def test_a_degenerate_hunk_does_not_crash():
    """A hunk header with no rows under it — malformed, but it must not take the
    turn down when all it should cost is an empty block."""
    rows(cli._diff_block(["--- a (old)", "+++ a", "@@ -1,0 +1,0 @@"], path="a.py"))


def test_a_hunk_lexes_each_side_against_its_own_file():
    """A removed line belongs to the old file and an added one to the new; lexing
    them together would colour a rewritten block off the wrong source."""
    old = 'text = """open\nstill a string\n"""\n'
    new = 'text = "closed"\nvalue = 1\n'
    body = "\n".join(rows(cli._diff_block(diff_of(old, new), path="x.py")))
    assert "still a string" in body
    assert "value = 1" in body
