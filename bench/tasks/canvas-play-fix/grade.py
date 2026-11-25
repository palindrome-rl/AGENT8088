"""Grader for canvas-play-fix.

    python grade.py <workspace> --json <report.json>

Opens game.html from disk in headless Chromium with every outside request blocked and plays the game: the arrow keys
move the paddle the right way and stop at the edge, and a click on the coin, where it is drawn on screen, scores and
moves it, while a click elsewhere does not. server.py must be unchanged. When no browser can be started the report
has no "passed" field, so the run counts as ungraded, not failed.
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))  # bench/, for graderlib
import graderlib

COINS = [[360, 80], [120, 140], [300, 220], [80, 60]]
TOTAL = 9


def grade(workspace: Path) -> dict:
    server = workspace / "server.py"
    server_unchanged = server.is_file() and server.read_bytes() == (HERE / "fixture" / "server.py").read_bytes()
    if not (workspace / "game.html").is_file():
        return graderlib.report({"server.py unchanged": server_unchanged, "game.html is there": False}, total=TOTAL)

    def play(session, checks):
        checks["server.py unchanged"] = server_unchanged
        checks["game.html is there"] = True
        tab = session.open(workspace / "game.html")
        tab.set_viewport_size({"width": 1000, "height": 700})

        def state():
            return tab.evaluate("({...window.game})")

        start = state()
        tab.keyboard.press("ArrowLeft")
        checks["ArrowLeft moves the paddle left"] = state()["paddle"] == start["paddle"] - 20
        tab.keyboard.press("ArrowRight")
        tab.keyboard.press("ArrowRight")
        checks["ArrowRight moves the paddle right"] = state()["paddle"] == start["paddle"] + 20
        for _ in range(40):
            tab.keyboard.press("ArrowLeft")
        checks["the paddle stops at the left edge"] = state()["paddle"] == 0
        frame = tab.evaluate("""() => { const b = document.getElementById('board'), r = b.getBoundingClientRect();
            return {x: r.x + b.clientLeft, y: r.y + b.clientTop, sx: b.clientWidth / b.width,
                    sy: b.clientHeight / b.height}; }""")

        def click(x, y):
            tab.mouse.click(frame["x"] + x * frame["sx"], frame["y"] + y * frame["sy"])

        click(200, 200)
        checks["a click away from the coin does not score"] = state()["score"] == 0
        click(*COINS[0])
        after = state()
        checks["a click on the coin scores"] = after["score"] == 1
        checks["the coin moves after a score"] = after["coin"] == 1
        click(*COINS[1])
        checks["the next coin scores too"] = state()["score"] == 2

    return graderlib.grade_in_browser(play, TOTAL)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("workspace")
    parser.add_argument("--json", required=True)
    args = parser.parse_args()
    report = grade(Path(args.workspace))
    Path(args.json).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
