"""A file named inside a sentence is attached without being cut out of it.

Live, 2026-09-24: the message
    Use execute_shell ... (1) grep -n -i -E "barcelona|madrid" artifacts/table.html ...
reached the model as `... (1) grep -n -i -E "barcelona|madrid"    (2) ...` plus
the file's text: the paste detector cut the path out of the user's words, and
the model ran grep with no file. The words are the instruction; the path
stays in them.
"""
import importlib
import sys

import pytest


@pytest.fixture
def cli(engine, monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT8088_CONFIG", str(tmp_path / "_no_such_config.txt"))
    if "agent8088.cli" in sys.modules:
        mod = importlib.reload(sys.modules["agent8088.cli"])
    else:
        from agent8088 import cli as mod
    mod.A = engine
    return mod


@pytest.fixture
def table(tmp_path, monkeypatch, engine):
    (tmp_path / "artifacts").mkdir()
    path = tmp_path / "artifacts" / "table.html"
    path.write_text("<tr><td>1</td><td>Barcelona</td><td>21</td></tr>\n")
    monkeypatch.chdir(tmp_path)
    engine.ALLOWED_PATHS = []
    return path


def _paste(cli, monkeypatch, line):
    seen = []
    monkeypatch.setattr(cli, "do_chat", seen.append)
    pasted = cli._detect_pasted_file(line)
    assert pasted, f"not detected: {line}"
    assert cli._handle_pasted_file(*pasted, original=line) is True
    return seen[0]


def test_a_command_naming_a_file_keeps_the_path(cli, table, monkeypatch):
    line = ('Use execute_shell to run: grep -n -i -E "barcelona|madrid" '
            'artifacts/table.html and tell me what it printed')
    query = _paste(cli, monkeypatch, line)
    assert query.startswith(line)
    assert "Barcelona" in query.split(line, 1)[1]   # the file is still attached


def test_a_question_about_a_file_keeps_the_path(cli, table, monkeypatch):
    query = _paste(cli, monkeypatch, "what is the top team in artifacts/table.html")
    assert query.startswith("what is the top team in artifacts/table.html")


def test_a_path_followed_by_a_question_keeps_both(cli, table, monkeypatch):
    query = _paste(cli, monkeypatch, "artifacts/table.html who is first?")
    assert query.startswith("artifacts/table.html who is first?")


def test_a_bare_path_still_gets_the_default_instruction(cli, table, monkeypatch):
    query = _paste(cli, monkeypatch, "artifacts/table.html")
    assert query.startswith("Here is the content of table.html.")


def test_without_the_original_line_the_old_call_shape_still_works(cli, table, monkeypatch):
    seen = []
    monkeypatch.setattr(cli, "do_chat", seen.append)
    cli._handle_pasted_file(table.resolve(), "who is first")
    assert seen[0].startswith("who is first")


def test_an_image_in_a_sentence_still_goes_to_image_handling(cli, tmp_path, monkeypatch):
    image = tmp_path / "photo.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    calls = []
    monkeypatch.setattr(cli, "cmd_image", lambda rest, resolver=None: calls.append(rest))
    line = f"describe this image {image}"
    pasted = cli._detect_pasted_file(line)
    assert cli._handle_pasted_file(*pasted, original=line) is True
    assert calls == [f"{image.resolve()} describe this image"]
