"""Pasting a bare file path into the prompt auto-reads it, without a tool
call and without going through ALLOWED_PATHS — because it's a path the user
personally typed, not something the model chose to read.

resolve_pasted_path is the security-relevant piece: it is the one place a
user's literal input is trusted more than a model-issued tool call. The floor
tests below prove it still refuses a credential file, and that this trust
does NOT extend to the model itself — a read_text tool call to the same
outside-ALLOWED_PATHS file is still refused exactly as before.
"""
import importlib
import json
import sys

import pytest


@pytest.fixture
def cli(engine, monkeypatch, tmp_path):
    """A fresh cli module, so its module-level state doesn't leak between
    tests, the same reasoning conftest.py's `engine` fixture uses."""
    monkeypatch.setenv("AGENT8088_CONFIG", str(tmp_path / "_no_such_config.txt"))
    if "agent8088.cli" in sys.modules:
        mod = importlib.reload(sys.modules["agent8088.cli"])
    else:
        from agent8088 import cli as mod
    mod.A = engine  # the cli module imports engine as A at load time; pin it
    return mod


# --- _detect_pasted_file: pure path logic, no I/O beyond existence checks --
def test_detects_an_existing_bare_path(cli, tmp_path):
    f = tmp_path / "report.docx"
    f.write_bytes(b"x")
    result = cli._detect_pasted_file(str(f))
    assert result == (f.resolve(), "")


def test_detects_a_path_with_a_trailing_question(cli, tmp_path):
    f = tmp_path / "report.docx"
    f.write_bytes(b"x")
    path, question = cli._detect_pasted_file(f'{f} what is the total revenue')
    assert path == f.resolve()
    assert question == "what is the total revenue"


def test_strips_surrounding_quotes_from_drag_and_drop(cli, tmp_path):
    f = tmp_path / "my report.docx"
    f.write_bytes(b"x")
    assert cli._detect_pasted_file(f'"{f}"') == (f.resolve(), "")


def test_a_path_mid_sentence_is_still_detected(cli, tmp_path):
    """describe this image <path> — not just <path> alone at the start."""
    f = tmp_path / "photo.png"
    f.write_bytes(b"x")
    path, question = cli._detect_pasted_file(f"describe this image {f}")
    assert path == f.resolve()
    assert question == "describe this image"


def test_a_quoted_path_with_a_space_mid_sentence_is_still_detected(cli, tmp_path):
    f = tmp_path / "my photo.png"
    f.write_bytes(b"x")
    path, question = cli._detect_pasted_file(f'what is in "{f}" exactly')
    assert path == f.resolve()
    assert question == "what is in  exactly"


def test_ordinary_chat_that_merely_looks_path_shaped_is_not_a_paste(cli, tmp_path):
    """The only guard against misfiring on real chat text: the candidate must
    resolve to a file that actually exists."""
    assert cli._detect_pasted_file("C:\\drive is full, what do I do") is None


def test_slash_commands_are_never_treated_as_a_paste(cli, tmp_path):
    assert cli._detect_pasted_file("/help") is None


def test_empty_line_is_not_a_paste(cli):
    assert cli._detect_pasted_file("   ") is None


# --- resolve_pasted_path: the security-relevant function -------------------
def test_resolve_pasted_path_works_outside_allowed_paths(engine, tmp_path):
    """The whole point of this feature: unlike resolve_user_path, a path the
    user typed by hand is not restricted to ALLOWED_PATHS."""
    engine.ALLOWED_PATHS = [tmp_path / "project_only"]
    outside = tmp_path / "elsewhere" / "report.docx"
    outside.parent.mkdir()
    outside.write_bytes(b"x")
    assert engine.resolve_pasted_path(str(outside)) == outside.resolve()


def test_the_model_itself_gains_nothing_from_this(engine, tmp_path):
    """A tool call to the same path the user could paste is still refused —
    proving this is a user-input trust boundary, not a widened permission."""
    engine.ALLOWED_PATHS = [tmp_path / "project_only"]
    engine.PERMISSION_MODE = "full-auto"
    outside = tmp_path / "elsewhere" / "report.docx"
    outside.parent.mkdir()
    outside.write_bytes(b"x")
    result = engine.exec_tool("read_text", json.dumps({"filename": str(outside)}))
    assert "Path not allowed" in result


def test_resolve_pasted_path_still_refuses_a_sensitive_file(engine, tmp_path):
    """The floor CLAUDE.md calls always-on: naming a path by hand is not
    evidence it isn't a credential."""
    engine.ALLOWED_PATHS = []  # even with no restriction at all
    secret = tmp_path / "id_rsa"
    secret.write_bytes(b"private key material")
    with pytest.raises(ValueError, match="sensitive"):
        engine.resolve_pasted_path(str(secret))


# --- /image itself, typed directly, gets the same trust as a bare paste ----
def test_cmd_image_resolves_outside_allowed_paths_by_default(cli, engine, tmp_path, monkeypatch, capsys):
    """/image <path> is the user typing a path by hand too — it must not be
    more restrictive than pasting the same path with no command at all."""
    f = tmp_path / "photo.png"
    f.write_bytes(b"\x89PNG\r\n\x1a\n")
    engine.ALLOWED_PATHS = [tmp_path / "elsewhere"]  # deliberately excludes f

    def reached_the_model(*a, **k):
        raise RuntimeError("reached the model call — path resolution succeeded")
    monkeypatch.setattr(cli.A, "create_completion", reached_the_model)

    cli.cmd_image(str(f))
    out = capsys.readouterr().out
    assert "Path not allowed" not in out
    assert "reached the model call" in out


# --- _handle_pasted_file: document branch -----------------------------------
def test_pasted_docx_is_extracted_and_handed_to_do_chat(cli, engine, tmp_path, monkeypatch):
    import zipfile
    docx = tmp_path / "report.docx"
    with zipfile.ZipFile(docx, "w") as zf:
        zf.writestr(
            "word/document.xml",
            '<?xml version="1.0"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:body><w:p><w:r><w:t>Q4 revenue was 45000</w:t></w:r></w:p></w:body></w:document>',
        )
    engine.ALLOWED_PATHS = []
    seen = {}
    monkeypatch.setattr(cli, "do_chat", lambda q: seen.setdefault("query", q))

    handled = cli._handle_pasted_file(docx, "")
    assert handled is True
    assert "Q4 revenue was 45000" in seen["query"]


def test_pasted_docx_with_a_question_uses_it_as_the_instruction(cli, engine, tmp_path, monkeypatch):
    import zipfile
    docx = tmp_path / "report.docx"
    with zipfile.ZipFile(docx, "w") as zf:
        zf.writestr(
            "word/document.xml",
            '<?xml version="1.0"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:body><w:p><w:r><w:t>content</w:t></w:r></w:p></w:body></w:document>',
        )
    engine.ALLOWED_PATHS = []
    seen = {}
    monkeypatch.setattr(cli, "do_chat", lambda q: seen.setdefault("query", q))

    cli._handle_pasted_file(docx, "what is the EU revenue")
    assert seen["query"].startswith("what is the EU revenue")


def test_pasted_sensitive_file_is_refused_not_leaked(cli, engine, tmp_path, monkeypatch, capsys):
    engine.ALLOWED_PATHS = []
    secret = tmp_path / "id_rsa"
    secret.write_bytes(b"private key material")
    called = []
    monkeypatch.setattr(cli, "do_chat", lambda q: called.append(q))

    handled = cli._handle_pasted_file(secret, "")
    assert handled is True  # handled = refused with a message, not passed to chat
    assert called == []


def test_pasted_unrecognized_binary_falls_through_to_chat(cli, engine, tmp_path):
    """A file that is neither an image nor an extractable document is left
    for do_chat to handle as ordinary text — no silent failure."""
    engine.ALLOWED_PATHS = []
    blob = tmp_path / "data.bin"
    blob.write_bytes(b"\x00\x01\x02\xff\xfe")
    assert cli._handle_pasted_file(blob, "") is False
