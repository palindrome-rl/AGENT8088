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


def test_setup_commands_are_the_ones_a_new_user_needs():
    listed = [command for command, _ in cli._SETUP_COMMANDS]
    assert listed[:4] == ["/model setup", "/mode", "/search", "/doctor"]


def test_the_two_groups_stay_close_in_length_and_lose_no_command():
    # The wide layout pads the shorter group; a group much longer would leave a gap in the other column.
    assert abs(len(cli._SETUP_COMMANDS) - len(cli._SESSION_COMMANDS)) <= 1
    assert cli._ESSENTIAL_COMMANDS == cli._SETUP_COMMANDS + cli._SESSION_COMMANDS


def test_every_essential_command_is_a_real_command():
    known = {entry["name"] for entry in cli.command_catalog()}
    for command, _ in cli._ESSENTIAL_COMMANDS:
        assert command.lstrip("/").split()[0] in known


@pytest.mark.parametrize("width", [140, 120, 110, 108, 100, 80, 75, 72, 69, 55, 40])
def test_banner_keeps_the_headings_and_drops_the_catalogues(width):
    rendered = _render(width)
    if width >= 70:
        assert "Available Tools" in rendered
        assert "Available Skills" in rendered
    # A tool and a skill name that the old catalogue printed in full.
    assert "write_file" not in rendered
    assert "spec-driven-development" not in rendered


@pytest.mark.parametrize("width", [140, 120, 110, 108, 100, 80, 75, 72, 69, 55, 40])
def test_banner_shows_the_essential_commands_at_every_width(width):
    rendered = _render(width)
    for command, _ in cli._ESSENTIAL_COMMANDS:
        # Narrow terminals wrap the run-on list, so match on the distinctive tail.
        assert command.split()[-1] in rendered


@pytest.mark.parametrize("width", [140, 120, 110, 108, 100, 80, 75, 72, 69, 55, 40])
def test_banner_never_overflows_the_terminal(width):
    for line in _render(width).splitlines():
        assert len(line) <= width, f"{width}: {line!r}"


def _column_of(rendered, needle):
    return [line.index(needle) for line in rendered.splitlines() if needle in line]


def test_one_block_layout_keeps_every_description_aligned():
    rendered = _render(100)
    columns = _column_of(rendered, "Add or switch models")
    assert columns, "100 columns should use the single-block layout"
    for _, description in cli._ESSENTIAL_COMMANDS:
        assert _column_of(rendered, description) == columns


def test_wide_layout_splits_the_groups_with_one_continuous_divider():
    rendered = _render(120)
    divider_rows = [line for line in rendered.splitlines() if cli._DIVIDER in line
                    and any(command in line for command, _ in cli._SESSION_COMMANDS)]
    assert len(divider_rows) == len(cli._SESSION_COMMANDS)
    # One column for every row is what makes the glyphs read as a single rule.
    assert len({line.index(cli._DIVIDER) for line in divider_rows}) == 1


def test_wide_layout_aligns_each_group_independently():
    rendered = _render(120)
    for group in (cli._SETUP_COMMANDS, cli._SESSION_COMMANDS):
        columns = _column_of(rendered, group[0][1])
        for _, description in group:
            assert _column_of(rendered, description) == columns


def test_layout_tier_is_chosen_by_available_width():
    assert isinstance(cli._essential_commands(cli._COMMANDS_MIN_WIDTH - 1), cli.Text)
    one_block = cli._essential_commands(cli._COMMANDS_MIN_WIDTH)
    assert not isinstance(one_block, cli.Text)
    assert len(one_block.columns) == 2
    assert len(cli._essential_commands(cli._COMMANDS_TWO_BLOCK_WIDTH).columns) == 5


def test_limits_is_listed_with_the_other_shortcuts():
    assert "/limits" in [command for command, _ in cli._ESSENTIAL_COMMANDS]
    for width in (140, 100, 60):
        assert "/limits" in _render(width)
