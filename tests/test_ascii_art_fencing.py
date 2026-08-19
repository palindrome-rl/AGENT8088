"""Box-drawn ASCII art must survive markdown rendering.

Rich's Markdown reflows bare paragraph text (collapsing padding, wrapping
lines), which destroys hand-drawn box art the moment a model emits it outside
a fenced code block or real table syntax. _fence_ascii_art repairs that by
wrapping any box-drawing-character lines in a fence before Rich ever sees
them, so their manual spacing is preserved verbatim.
"""

from agent8088.cli import _fence_ascii_art

BOX = (
    "╔══╗\n"
    "║ 2 ║\n"
    "╚══╝"
)


def test_bare_box_art_gets_fenced():
    result = _fence_ascii_art(BOX)
    lines = result.split("\n")
    assert lines[0] == "```"
    assert lines[-1] == "```"
    assert lines[1:-1] == BOX.split("\n")


def test_already_fenced_box_art_is_untouched():
    text = f"```\n{BOX}\n```"
    assert _fence_ascii_art(text) == text


def test_prose_around_the_box_is_left_as_bare_paragraph():
    text = f"Here is a table:\n\n{BOX}\n\nDone."
    result = _fence_ascii_art(text)
    assert result.split("\n")[0] == "Here is a table:"
    assert result.rstrip().split("\n")[-1] == "Done."
    assert "```" in result


def test_text_without_box_characters_is_untouched():
    text = "Just a normal reply with **bold** and a | pipe | in it."
    assert _fence_ascii_art(text) is text


def test_real_gfm_table_has_no_box_chars_and_is_untouched():
    text = "| Expr | Result |\n|------|--------|\n| 2 x 1 | 2 |"
    assert _fence_ascii_art(text) is text


def test_empty_and_none_are_returned_as_is():
    assert _fence_ascii_art("") == ""
    assert _fence_ascii_art(None) is None


def test_two_separate_boxes_get_two_separate_fences():
    text = f"{BOX}\n\nsome text between\n\n{BOX}"
    result = _fence_ascii_art(text)
    assert result.count("```") == 4
