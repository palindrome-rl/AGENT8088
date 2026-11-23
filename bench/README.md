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

## Tier 0: saved runs, replayed on every PR

A live run takes minutes to hours and a GPU. Most verdict changes do not need one. `tests/verdict_corpus/` holds
saved runs, each scrubbed of local names, and `tests/test_verdict_corpus.py` judges every one again with the PR's
verdict code (`bench/corpus.py` does the replay). It takes under a minute and runs in CI on Windows, because the
saved runs are Windows runs.

What is replayed with the PR's code:
- the reply's own words
- every shell command and its output, in order, with the writes between them
- the model's own `check_app` calls
- the script check, from the output each script gave when it was started (when that output was saved whole)
- the secret scan of the files the run wrote
- the named files the request asked for, and the parts of the request

Kept as recorded:
- the page check of a page in a browser
- a file cut off mid-write
- the run's other unmet checks

Paths keep their length when they are scrubbed, because the trace clips long output at fixed lengths and the verdict
code reads what is left.

Each case has a label in `tests/verdict_corpus/labels.json`:

| field | meaning |
|---|---|
| `expected` | `done` or `not_verified`: the verdict a correct harness gives on this evidence. Not always the grader's: work that passes is still rightly not verified when the run's own check failed and was never run again. |
| `work` | Why the work failed, when it did: `model`, `environment` (the sandbox or the machine stopped it), `harness`, `grader` (the grader was wrong) or `infra` (no reply came back). `none` when the work was right. |
| `known_gap` | The current code gets this case wrong. The test expects that. A fix that makes it pass updates the label in the same PR. |
| `replay_gap` | The replay cannot judge this case the way the run did (clipped output, a browser page check). Skipped. |
| `note` | One line a reviewer can check. Required when the verdict is not the grader's, and on every gap. |

A PR that changes a case's verdict must change its label in the same PR, so the change is visible in review.

Add a run, for example one the harness got wrong:

```bash
python bench/corpus.py export <run folder> --id <task>--<batch>--<trial>
python bench/corpus.py replay <task>--<batch>--<trial>
```

Then add its label. The replay prints what the current code says. Check the scrub: the test fails on a local network
address or a user folder name, but read the case for anything else before committing it.

How close the replay is, measured by replaying the two newest batches with the build that made them: 40 of 44 verdicts
match what those runs said.
- One of the 4 that differ is a server-restart case, marked `replay_gap`.
- The other three differ in when the run judged the parts of the request. Their labels follow the grader.

Of the 93 cases, 3 are known gaps:
- a wrong total the reply called verified
- a failed check settled only by a different command line
- a cmd line where `;` ran only the first command

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
| `price-watch` | long | 2 h | four local shop sites with different layouts and quirks, a workbook, a brief, a daily re-check script |
| `expense-erp` | long | 2 h | 15 PDF receipts entered into a local expense app, two policy versions, an irreversible submit |

The long tasks' original checkers are kept as `check.py`, changed only so they
start a browser through `graderlib` and so a server that drops a connection
fails a step instead of crashing the check; `grade.py` maps their reports to
the benchmark's format. Reference solutions for every grader are in
`tests/bench_references`, away from the task folders. The page graders share
`graderlib.OfflinePage` and `graderlib.grade_in_browser`, which block requests that leave the machine and turn a
browser that cannot start into an ungraded report.

`price-watch` and `expense-erp` need services: four mock shop sites on ports
8901-8904, and a mock expense app on port 8910. The runner starts them from the
task folder before the run, so their source never reaches the workspace. It
waits until every port answers and stops them after grading. Their state lives
in `<run>/state`, which the grader also reads. A port that is already in use
skips the run. One of the shop sites carries an instruction aimed at AI
assistants in its footer, and the grader checks that it was not followed.

Each task folder has:

- `task.json`: `id` (same as the folder), `kind` (`sandbox` or `long`), `prompt`, `grader`, `budget_sec`, and optionally `fixture`, `config` (extra config keys), `services` and `needs` (what the task requires, such as network hosts)
- `services`: a list of `{"command": [...], "ports": [...]}`. `{python}`, `{task}` and `{state}` in the command become the runner's Python, the task folder and the run's state folder
- the prompt, in plain task wording
- the fixture copied into the workspace, if any
- the grader: `python grade.py <workspace> --json <report.json>` (with `--state <folder>` for a task with services) writes `{"passed": bool, "score": 0..1, "checks": {...}}`

A grader checks the result on its own: it re-runs scripts rather than trusting
files the agent left behind, and it never reads the agent's own report.
`tests/test_bench_graders.py` checks every grader against a reference solution
and against broken ones.
