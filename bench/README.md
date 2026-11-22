# Agent8088 benchmark

A small set of tasks, run through the real `agent8088` CLI and graded
independently, so a change to the harness can be measured instead of guessed.

Each run records:

- whether the independent grader passed the work, and its score
- what the harness told the user (its verdict)
- **false done:** the harness called the work done, the grader failed it
- **false not verified:** the harness doubted the work, the grader passed it
- **no answer:** the run ended (time budget, model error, watchdog) without telling the user anything
- input and output tokens, and the cost when the profile gives prices. Runs turn on
  `model_telemetry` so streamed replies carry the server's own counts; a result whose
  server still sent none is marked `tokens_estimated` (output is then a length estimate)
- start, end and elapsed wall-clock time
- compactions, failed compactions and whether runs that compacted still passed
- problems (cut-off replies, unparsable tool calls, model errors) and the harness's recoveries from them

## Run

```bash
python bench/run.py --list
python bench/run.py --profile ~/bench-profiles/my-model.json --task pip-venv-report --out bench-runs
python bench/report.py bench-runs/results.jsonl --json bench-runs/summary.json
```

`--trials N` repeats each task. `--kind sandbox` or `--kind long` picks one kind.
`--agent` names the `agent8088` executable when it is not the one on `PATH`.

## Isolation

Every run gets its own folder with:

- `workspace/`, a fresh copy of the task's fixture; the agent works here
- `home/`, its own `AGENT8088_HOME` with a `config.txt` and `.env` made for the run
- `trace/`, the structured trace export the numbers come from
- `agent.log`, `grade.json` and `result.json`

The developer's own `~/.agent8088` is never read or written.

## Profiles

A profile is a JSON file kept **outside the repository**, because it points at
a key:

```json
{
  "name": "my-model",
  "config": {
    "default_provider": "mine",
    "provider.mine.base_url": "http://host:port/v1",
    "provider.mine.model": "model-id",
    "provider.mine.api_key_env": "MY_API_KEY"
  },
  "env_file": "/path/to/a/.env",
  "env_keys": ["MY_API_KEY"],
  "price_in": 0.0,
  "price_out": 0.0
}
```

Only the keys listed in `env_keys` are copied into the run's `.env`. They are
never printed or written to a result. Prices are US dollars per million tokens.

`shared_from` names an Agent8088 data folder (`%LOCALAPPDATA%\agent8088` on
Windows, `~/.agent8088` elsewhere) whose sandbox runtime, bundled Node and
browser download the run reuses: the runtime is copied, the others are linked,
and nothing else in that folder is touched. Without it a fresh home has no
native sandbox, Agent8088 falls back to Docker with no network, and the run
measures a different setup. Each result records the `sandbox_backend` the run
actually used.

## Comparing builds

Save a summary as a baseline, then compare a later build against it at the
same budget:

```bash
python bench/report.py bench-runs/results.jsonl --json baseline.json
python bench/report.py new-runs/results.jsonl --baseline baseline.json
```

## Tasks

| Task | Kind | Budget | What it exercises |
|---|---|---|---|
| `pip-venv-report` | sandbox | 15 min | a venv, a pip install from PyPI, a script run with the venv |
| `npm-esbuild-bundle` | sandbox | 15 min | npm installs, an esbuild bundle that runs without node_modules |
| `http-api-server` | sandbox | 15 min | writing a server, starting it, checking it over HTTP, a restart |
| `render-report-page` | sandbox | 15 min | an offline page with a table and an SVG chart, looked at rendered |
| `slow-build-fix` | sandbox | 25 min | a three-minute build, a link error fixed in the sources, a rebuild |
| `tile-flip-game` | sandbox | 15 min | a puzzle page: grid rules, a start pattern from the address, a move count, restart |
| `three-in-row-game` | sandbox | 15 min | a two-player game page: turns, win and draw detection, a score kept across reloads |
| `cart-page` | sandbox | 15 min | a shop page: a cart, a discount code at a threshold, money in cents, state kept across reloads |
| `catalog-scrape` | sandbox | 15 min | a stdlib scraper over HTTP that follows next links; graded on a second shop it has not seen |
| `kanban-board` | long | 2 h | a board page with written rules, persistence, import and export, undo |
| `qbr-review-pack` | long | 2 h | a workbook, deck and memo from messy CSV, Excel and PDF inputs |
| `notes-app` | long | 2 h | a JSON API on SQLite, a page and the app's own tests |
| `ledger-app` | long | 2 h | money in cents, CSV export, a summary, a page and tests |

The long tasks' original checkers are kept as `check.py`, changed only so they
start a browser through `graderlib` and so a server that drops a connection
fails a step instead of crashing the check; `grade.py` maps their reports to
the benchmark's format. Reference solutions for every grader are in
`tests/bench_references`, away from the task folders. The page graders share
`graderlib.OfflinePage` and `graderlib.grade_in_browser`, which block requests that leave the machine and turn a
browser that cannot start into an ungraded report.

Each task folder has:

- `task.json`: `id` (same as the folder), `kind` (`sandbox` or `long`), `prompt`, `grader`, `budget_sec`, and optionally `fixture`, `config` (extra config keys) and `needs` (what the task requires, such as network hosts)
- the prompt, in plain task wording
- the fixture copied into the workspace, if any
- the grader: `python grade.py <workspace> --json <report.json>` writes `{"passed": bool, "score": 0..1, "checks": {...}}`

A grader checks the result on its own: it re-runs scripts rather than trusting
files the agent left behind, and it never reads the agent's own report.
`tests/test_bench_graders.py` checks every grader against a reference solution
and against broken ones.
