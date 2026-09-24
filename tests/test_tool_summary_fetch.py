"""The call header must not report a download as an empty file.

A write_file that fetches from source_url sends content "" and the header read
"la_liga_2026_27.html (0 lines, 0 B)" while the tool wrote 581,506 bytes.
"""
import importlib

import pytest


@pytest.fixture
def cli(engine):
    from agent8088 import cli as module
    return importlib.reload(module)


def test_a_fetch_names_its_source_not_an_empty_size(cli):
    summary = cli._tool_summary("write_file", {
        "filename": "la_liga_2026_27.html", "content": "",
        "source_url": "https://en.wikipedia.org/wiki/2026%E2%80%9327_La_Liga"})
    assert summary == "la_liga_2026_27.html ← en.wikipedia.org"


def test_a_fetch_without_a_content_argument_names_its_source(cli):
    summary = cli._tool_summary("write_file", {
        "filename": "logo.png", "source_url": "https://example.com/img/logo.png"})
    assert summary == "logo.png ← example.com"


def test_content_wins_over_source_url_as_it_does_in_the_engine(cli):
    """The engine fetches only when content is empty, so the header must agree."""
    summary = cli._tool_summary("write_file", {
        "filename": "notes.txt", "content": "a\nb", "source_url": "https://example.com/x"})
    assert summary == "notes.txt (2 lines, 3 B)"


def test_a_real_empty_write_still_says_so(cli):
    assert cli._tool_summary("write_file", {"filename": "empty.txt", "content": ""}) == (
        "empty.txt (0 lines, 0 B)")


def test_an_unparseable_source_url_falls_back_to_the_filename(cli):
    assert cli._tool_summary("write_file", {
        "filename": "x.bin", "content": "", "source_url": "not a url"}) == "x.bin"
