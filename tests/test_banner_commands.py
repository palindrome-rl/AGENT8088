"""The banner lists a handful of must-know commands instead of every tool and skill."""

import io

import pytest
from rich.console import Console

from agent8088 import cli


def _render(width):
    console = Console(file=io.StringIO(), width=width, force_terminal=False, legacy_windows=False)
    original = cli.console
    cli.console = console
    try:
        cli.banner()
    finally:
        cli.console = original
    return console.file.getvalue()


def test_essential_commands_are_the_ones_a_new_user_needs():
    listed = [command for command, _ in cli._ESSENTIAL_COMMANDS]
    assert listed[:4] == ["/model setup", "/mode", "/search", "/doctor"]


def test_every_essential_command_is_a_real_command():
    known = {entry["name"] for entry in cli.command_catalog()}
    for command, _ in cli._ESSENTIAL_COMMANDS:
        assert command.lstrip("/").split()[0] in known


@pytest.mark.parametrize("width", [140, 120, 100, 80, 72, 69, 55, 40])
def test_banner_keeps_the_headings_and_drops_the_catalogues(width):
    rendered = _render(width)
    if width >= 70:
        assert "Available Tools" in rendered
        assert "Available Skills" in rendered
    # A tool and a skill name that the old catalogue printed in full.
    assert "write_file" not in rendered
    assert "spec-driven-development" not in rendered


@pytest.mark.parametrize("width", [140, 120, 100, 80, 72, 69, 55, 40])
def test_banner_shows_the_essential_commands_at_every_width(width):
    rendered = _render(width)
    for command, _ in cli._ESSENTIAL_COMMANDS:
        # Narrow terminals wrap the run-on list, so match on the distinctive tail.
        assert command.split()[-1] in rendered


@pytest.mark.parametrize("width", [140, 120, 100, 80, 72, 69, 55, 40])
def test_banner_never_overflows_the_terminal(width):
    for line in _render(width).splitlines():
        assert len(line) <= width, f"{width}: {line!r}"


def test_descriptions_stay_aligned_while_they_are_shown():
    rendered = _render(100)
    columns = [line.index("Add or switch a provider") for line in rendered.splitlines()
               if "Add or switch a provider" in line]
    assert columns, "the two-column layout should be used at 100 columns"
    for _, description in cli._ESSENTIAL_COMMANDS:
        matches = [line.index(description) for line in rendered.splitlines() if description in line]
        assert matches == columns


def test_descriptions_are_dropped_rather_than_wrapped_when_space_is_tight():
    assert isinstance(cli._essential_commands(cli._COMMANDS_MIN_WIDTH - 1), cli.Text)
    assert not isinstance(cli._essential_commands(cli._COMMANDS_MIN_WIDTH), cli.Text)
