"""Box-drawn ASCII art must survive markdown rendering.

Rich's Markdown reflows bare paragraph text (collapsing padding, wrapping
lines), which destroys hand-drawn box art the moment a model emits it outside
a fenced code block or real table syntax. _fence_ascii_art repairs that by
wrapping any box-drawing-character lines in a fence before Rich ever sees
them, so their manual spacing is preserved verbatim.

Fencing alone doesn't fix a model that miscounts its own padding — it just
preserves the mistake exactly as typed. _normalize_box_row_widths repairs
that separate failure mode by re-padding any bordered content row that came
in short of the block's own frame width.
"""

from agent8088.cli import _fence_ascii_art, _normalize_box_row_widths

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


# Real output captured from a live local model (ornith-1.0-35b) against the
# "retro terminal card" prompt: the frame/title/footer are all 19 chars wide,
# but every body row is consistently 18 — one space short before the border.
_LIVE_MODEL_BOX = (
    "```\n"
    "╔═════════════════╗\n"
    "║  TABLE OF TWO   ║\n"
    "╠═════════════════╣\n"
    "║2 ×  1 =  2 ★   ║\n"
    "║2 × 10 = 20 ★   ║\n"
    "╠═════════════════╣\n"
    "║ MULTIPLICATION  ║\n"
    "╚═════════════════╝\n"
    "```"
)


def test_short_body_rows_are_padded_to_the_frame_width():
    result = _fence_ascii_art(_LIVE_MODEL_BOX)
    lengths = {len(line) for line in result.split("\n") if line != "```"}
    assert lengths == {19}


def test_padding_is_inserted_before_the_closing_border_not_after():
    result = _fence_ascii_art(_LIVE_MODEL_BOX)
    for line in result.split("\n"):
        if line.startswith("║2"):
            assert line.endswith(" ║")


def test_normalizing_is_idempotent():
    once = _fence_ascii_art(_LIVE_MODEL_BOX)
    twice = _fence_ascii_art(once)
    assert once == twice


def test_a_block_with_no_pure_frame_line_is_left_alone():
    # No line made entirely of border characters, so there's no ground truth
    # width to normalize to — leave the (possibly ragged) content untouched.
    lines = ["║ short ║", "║ a longer row ║"]
    assert _normalize_box_row_widths(lines) == lines


def test_rows_longer_than_the_frame_are_never_truncated():
    lines = ["╔═══╗", "║ this row is way too long for the frame ║", "╚═══╝"]
    result = _normalize_box_row_widths(lines)
    assert result[1] == lines[1]


def test_normalization_also_runs_inside_a_fence_the_model_already_wrote():
    # _fence_ascii_art must not skip normalization just because the model
    # already fenced its own (still-broken) box.
    result = _fence_ascii_art(_LIVE_MODEL_BOX)
    assert result.count("```") == 2
    lengths = {len(line) for line in result.split("\n") if line != "```"}
    assert lengths == {19}
