"""Grader for three-in-row-game.

    python grade.py <workspace> --json <report.json>

Opens board.html from disk in headless Chromium with every outside request
blocked, plays an X win, a draw and an O win, reloads to check the saved score,
and checks that a filled cell and a finished board ignore clicks. When no
browser can be started the report has no "passed" field, so the run counts as
ungraded, not failed.
"""
import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))  # bench/, for graderlib
import graderlib

TOTAL = 10


def text(page, selector: str) -> str:
    return page.evaluate(f"() => {{ const e = document.querySelector('{selector}'); return e ? e.innerText : ''; }}")


def number(page, selector: str):
    digits = "".join(ch for ch in text(page, selector) if ch.isdigit())
    return int(digits) if digits else None


def cells(page) -> list:
    found = page.evaluate("""() => [...document.querySelectorAll('[data-cell]')]
        .map(c => [Number(c.dataset.cell), c.innerText.trim().toUpperCase()])""")
    board = [""] * 9
    for index, mark in found:
        if 0 <= index < 9:
            board[index] = mark
    return board if len(found) == 9 else [f"<{len(found)} cells>"]


def status(page) -> str:
    return " ".join(text(page, "#status").lower().split())


def play_moves(page, moves):
    for index in moves:
        page.click(f'[data-cell="{index}"]', timeout=3000)
        page.wait_for_timeout(30)


def scores(page):
    return number(page, "#score-x"), number(page, "#score-o"), number(page, "#score-draw")


def new_game(page):
    page.click("#new-game", timeout=3000)
    page.wait_for_timeout(50)


def play(session, checks: dict, page_file: Path):
    page = session.open(page_file)
    checks["nine empty cells and X to move"] = cells(page) == [""] * 9 and status(page) == "x to move"
    play_moves(page, [0])
    checks["turns alternate"] = cells(page)[0] == "X" and status(page) == "o to move"
    play_moves(page, [0])
    checks["a filled cell ignores clicks"] = cells(page)[0] == "X" and status(page) == "o to move"
    play_moves(page, [1, 4, 2, 8])  # X: 0 4 8, the diagonal
    checks["a diagonal wins"] = status(page) == "x wins"
    play_moves(page, [3])
    checks["a finished board ignores clicks"] = cells(page)[3] == ""
    new_game(page)
    checks["new game clears the board and keeps the score"] = (cells(page) == [""] * 9 and status(page) == "x to move"
                                                              and scores(page) == (1, 0, 0))
    play_moves(page, [0, 1, 2, 4, 3, 5, 7, 6, 8])
    checks["a full board with no line is a draw"] = status(page) == "draw" and scores(page) == (1, 0, 1)
    new_game(page)
    play_moves(page, [0, 3, 1, 4, 8, 5])  # O: 3 4 5, the middle row
    checks["O can win a row"] = status(page) == "o wins" and scores(page) == (1, 1, 1)
    page.reload()
    page.wait_for_timeout(300)
    checks["the scores survive a reload"] = scores(page) == (1, 1, 1)
    checks["no console errors or outside requests"] = not session.errors and not session.outside


def grade(workspace: Path) -> dict:
    page_file = workspace / "board.html"
    if not page_file.is_file():
        return graderlib.report({"board.html exists": False}, total=TOTAL)
    return graderlib.grade_in_browser(lambda session, checks: play(session, checks, page_file), TOTAL)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("workspace")
    parser.add_argument("--json", required=True)
    args = parser.parse_args()
    graderlib.write(grade(Path(args.workspace)), args.json)


if __name__ == "__main__":
    sys.exit(main())
