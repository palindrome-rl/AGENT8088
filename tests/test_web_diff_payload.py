import difflib

from agent8088 import diffview


def _diff(old, new, name="src/app.py"):
    return list(difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile=f"{name} (old)", tofile=name, lineterm="",
    ))


def test_result_payload_carries_the_diff(monkeypatch):
    from agent8088 import web_server

    monkeypatch.setattr(web_server, "_result_diff",
                        lambda: diffview.to_payload(_diff("a\nb\n", "a\nB\n")))
    payload = web_server._tool_result_payload(
        "edit_file", "Updated src/app.py with 1 addition and 1 removal")
    assert payload["type"] == "tool_result"
    assert payload["name"] == "edit_file"
    assert payload["diff"]["file"] == "src/app.py"
    assert payload["diff"]["added"] == 1
    assert payload["diff"]["removed"] == 1


def test_no_diff_field_when_nothing_was_written(monkeypatch):
    from agent8088 import web_server

    monkeypatch.setattr(web_server, "_result_diff", lambda: None)
    payload = web_server._tool_result_payload("web_search", "3 results")
    assert "diff" not in payload


def test_result_text_is_still_scrubbed_and_capped(monkeypatch):
    from agent8088 import web_server

    monkeypatch.setattr(web_server, "_result_diff", lambda: None)
    payload = web_server._tool_result_payload("read_text", "x" * 9000)
    assert len(payload["result"]) <= 5000


def test_a_broken_diff_never_breaks_the_event(monkeypatch):
    from agent8088 import web_server

    monkeypatch.setattr(web_server, "_result_diff",
                        lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    # The result event matters more than the diff; a diff failure must not
    # take the tool result down with it.
    try:
        payload = web_server._tool_result_payload("edit_file", "Updated x")
    except RuntimeError:
        raise AssertionError("_tool_result_payload must not propagate a diff failure")
    assert "diff" not in payload
