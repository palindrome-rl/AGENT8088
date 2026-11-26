"""Grader for tile-flip-game.

    python grade.py <workspace> --json <report.json>

Opens game.html from disk in headless Chromium with every outside request
blocked, plays fixed click sequences and compares the board with its own model
of the rules. When no browser can be started the report has no "passed" field,
so the run counts as ungraded, not failed.
"""
import argparse
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))  # bench/, for graderlib
import graderlib

DEFAULT = "0000000100011100010000000"
TOTAL = 9


def flip(board: str, row: int, col: int) -> str:
    cells = list(board)
    for r, c in ((row, col), (row - 1, col), (row + 1, col), (row, col - 1), (row, col + 1)):
        if 0 <= r < 5 and 0 <= c < 5:
            cells[r * 5 + c] = "1" if cells[r * 5 + c] == "0" else "0"
    return "".join(cells)


def read_board(page) -> str:
    cells = page.evaluate("""() => [...document.querySelectorAll('[data-row][data-col]')]
        .map(t => [Number(t.dataset.row), Number(t.dataset.col), String(t.dataset.lit)])""")
    if len(cells) != 25:
        return f"<{len(cells)} tiles>"
    board = ["?"] * 25
    for row, col, lit in cells:
        if 0 <= row < 5 and 0 <= col < 5:
            board[row * 5 + col] = lit if lit in ("0", "1") else "?"
    return "".join(board)


def text(page, selector: str) -> str:
    return page.evaluate(f"() => {{ const e = document.querySelector('{selector}'); return e ? e.innerText : ''; }}")


def moves(page):
    digits = "".join(ch for ch in text(page, "#moves") if ch.isdigit())
    return int(digits) if digits else None


def click(page, row: int, col: int):
    page.click(f'[data-row="{row}"][data-col="{col}"]', timeout=3000)
    page.wait_for_timeout(50)


def says_solved(status: str) -> bool:
    """The status says Solved. "Not solved" and "Unsolved" say the opposite."""
    words = status.lower()
    return bool(re.search(r"\bsolved\b", words)) and not re.search(r"\b(?:not|un)[\s-]*solved\b", words)


def grade(workspace: Path) -> dict:
    page_file = workspace / "game.html"
    if not page_file.is_file():
        return graderlib.report({"game.html exists": False}, total=TOTAL)
    return graderlib.grade_in_browser(lambda session, checks: play(session, checks, page_file), TOTAL)


def play(session, checks: dict, page_file: Path):
    page = session.open(page_file)
    checks["25 tiles with the default start pattern"] = read_board(page) == DEFAULT
    checks["not solved at the start"] = not says_solved(text(page, "#status"))
    click(page, 2, 2)
    checks["a click flips the tile and its four neighbours"] = read_board(page) == "0" * 25
    checks["solved when no tile is lit"] = "solved" in text(page, "#status").lower() and moves(page) == 1
    click(page, 0, 0)
    checks["clicks after solving change nothing"] = read_board(page) == "0" * 25 and moves(page) == 1

    start = "1000100000001000000010001"
    page = session.open(page_file, "?start=" + start)
    expected = start
    for row, col in ((0, 0), (4, 4), (2, 0), (1, 3), (0, 4), (3, 2)):
        click(page, row, col)
        expected = flip(expected, row, col)
    checks["edge and corner clicks follow the rules"] = read_board(page) == expected
    checks["the move count counts clicks"] = moves(page) == 6
    page.click("#restart", timeout=3000)
    page.wait_for_timeout(50)
    checks["restart puts the start pattern back"] = read_board(page) == start and moves(page) == 0
    checks["no console errors or outside requests"] = not session.errors and not session.outside


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("workspace")
    parser.add_argument("--json", required=True)
    args = parser.parse_args()
    graderlib.write(grade(Path(args.workspace)), args.json)


if __name__ == "__main__":
    sys.exit(main())
