"""The request's parts are checked against words that really are in the delivered files, and a part that asks for
something to be run is met by a command that ran.

Seen in a benchmark run: the package.json the run wrote held `"build": "esbuild src/index.js --bundle
--outfile=dist/app.js"` and `"dayjs"`, but a key word counted only after a space, so "build", "dayjs", "index", "dist"
and "node" all read as missing. All three parts of a finished request were reported as not addressed, every completion
check went on them, and the run ended NOT VERIFIED although the grader passed it."""
from agent8088 import completion as cc
from agent8088.trajectory import TrajectoryState

PACKAGE = """{
  "name": "npm-esbuild-bundle",
  "scripts": {
    "build": "esbuild src/index.js --bundle --platform=node --outfile=dist/app.js"
  },
  "dependencies": {
    "dayjs": "^1.11.23"
  },
  "devDependencies": {
    "esbuild": "^0.28.2"
  }
}
"""
PARTS = [
    "Create package.json, install `dayjs` as a dependency and `esbuild` as a dev dependency from the npm registry.",
    "Add an npm script called `build` that uses esbuild to bundle src/index.js into one file, dist/app.js, for Node, "
    "with dayjs included in the bundle so dist/app.js runs on its own.",
    "Run `npm run build`, then run `node dist/app.js` and check what it prints.",
]


def test_words_after_quotes_slashes_and_equals_are_words():
    words = cc._words_for_evidence('"build": "esbuild src/index.js --outfile=dist/app.js" "dayjs" devDependencies')
    for word in ("build", "esbuild", "src", "index", "outfile", "dist", "app", "dayjs", "dev", "dependencies"):
        assert f" {word} " in f" {words} ", word


def test_the_finished_request_from_the_run_is_addressed():
    assert cc.unaddressed_subgoals(PARTS, "", ["package.json"], evidence=PACKAGE) == []


def test_an_empty_run_still_leaves_every_part_open():
    assert cc.unaddressed_subgoals(PARTS, "All done.", [], evidence=" ") == PARTS


def test_a_part_to_run_something_is_met_by_the_command_that_ran():
    part = ["Run the migration script and start the scheduler."]
    assert cc.unaddressed_subgoals(part, "", ["notes.md"], evidence="notes") == part
    ran = ["python migrate.py --all", "python scheduler.py --start"]
    assert cc.unaddressed_subgoals(part, "", ["notes.md"], evidence="notes", ran=ran) == []


def test_a_part_to_build_something_is_never_met_by_a_command_line():
    part = ["Add a CSV export button and a monthly summary chart."]
    ran = ["echo CSV export button monthly summary chart"]
    assert cc.unaddressed_subgoals(part, "", ["index.html"], evidence="<html></html>", ran=ran) == part


def test_a_claim_in_the_reply_still_does_not_count_for_a_build_part():
    part = ["Add a CSV export button and a monthly summary chart."]
    reply = "I added a CSV export button and a monthly summary chart."
    assert cc.unaddressed_subgoals(part, reply, ["index.html"], evidence="<html></html>") == part


def _shell(state, command, result, *, failed=False, verdict="unknown"):
    operation = state.before_tool("execute_shell")
    state.after_tool(operation, result, failed=failed, blocked=False, mutated=False, verdict=verdict, command=command)


def test_the_trajectory_keeps_commands_that_finished_cleanly():
    state = TrajectoryState({}, goal="g", message_count=1)
    _shell(state, "npm run build", "dist/app.js  14.4kb\nDone in 9ms")
    _shell(state, "node dist/app.js", "Traceback (most recent call last):\n  ...\nValueError: x")
    _shell(state, "python broken.py", "Error: no such file", failed=True, verdict="failed")
    _shell(state, "node dist/app.js", "Command exited with status 1.")
    _shell(state, "npm run build", "Done in 9ms")
    assert state.ran_commands() == ["npm run build"]
