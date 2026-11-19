from agent8088 import patching


def test_unique_exact_match_applies_and_reports_its_line():
    result = patching.apply_edit("a\nb\nc\n", "b", "B")
    assert result.ok
    assert result.text == "a\nB\nc\n"
    assert result.line == 2
    assert result.strategy == "exact"


def test_multiline_old_string_applies():
    result = patching.apply_edit(
        "def run(x):\n    return x\n", "def run(x):\n    return x", "def run(x):\n    return x * 2")
    assert result.ok
    assert result.text == "def run(x):\n    return x * 2\n"


def test_empty_new_string_deletes():
    result = patching.apply_edit("keep\ndrop\n", "drop\n", "")
    assert result.ok
    assert result.text == "keep\n"


def test_no_match_refuses_and_names_the_closest_line():
    result = patching.apply_edit("def run(x):\n    return x\n", "    return y", "    return z")
    assert not result.ok
    assert result.text is None
    assert "not found" in result.error
    # The near-miss is the actionable half: the model needs the real text.
    assert "return x" in result.error
    assert "line 2" in result.error


def test_ambiguous_match_refuses_with_the_count_and_locations():
    result = patching.apply_edit("x = 1\ny = 2\nx = 1\n", "x = 1", "x = 3")
    assert not result.ok
    assert result.text is None
    assert "2 times" in result.error
    assert "line 1" in result.error and "line 3" in result.error
    assert "surrounding" in result.error


def test_whitespace_normalized_fallback_applies_and_labels_itself():
    # Model emitted trailing whitespace the file does not have.
    result = patching.apply_edit("a\n    return x\nc\n", "    return x   ", "    return y")
    assert result.ok
    assert result.strategy == "normalized"
    assert result.text == "a\n    return y\nc\n"


def test_normalized_fallback_still_requires_uniqueness():
    result = patching.apply_edit("    return x\n    return x\n", "    return x  ", "    return y")
    assert not result.ok
    assert "2 times" in result.error


def test_normalized_fallback_refuses_a_mid_line_match():
    # "return x" squeeze-matches inside "    return x", but rebuilding by whole
    # lines would drop the indentation. Refuse rather than mangle the line.
    result = patching.apply_edit("a\n    return x\nc\n", "return x ", "return y")
    assert not result.ok
    assert "not found" in result.error


def test_empty_old_string_points_at_write_file():
    result = patching.apply_edit("a\n", "", "b")
    assert not result.ok
    assert "write_file" in result.error


def test_noop_edit_is_refused():
    result = patching.apply_edit("a\n", "a", "a")
    assert not result.ok
    assert "identical" in result.error


def test_crlf_file_keeps_its_line_endings():
    result = patching.apply_edit("a\r\nb\r\nc\r\n", "b", "B")
    assert result.ok
    assert result.text == "a\r\nB\r\nc\r\n"
    assert "\n" not in result.text.replace("\r\n", "")


def test_dominant_newline():
    assert patching.dominant_newline("a\r\nb\r\n") == "\r\n"
    assert patching.dominant_newline("a\nb\n") == "\n"
    assert patching.dominant_newline("") == "\n"
