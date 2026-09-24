# Agent8088 on Terminal-Bench 2.1

Runs Agent8088 on [Terminal-Bench 2.1](https://github.com/harbor-framework/terminal-bench-2-1)
with [Harbor](https://github.com/harbor-framework/harbor), against a local
Qwen3.8-27B served by vLLM. Harbor installs Agent8088 into each task container
and starts it with one instruction. The task's verifier then grades what it
left behind.

## Files

| File | Purpose |
|---|---|
| `agent8088_harbor/agent.py` | Harbor installed-agent adapter |
| `container/run-agent.sh` | Launcher inside the container, with a time-budget watchdog |
| `config.txt` | Agent8088 profile: model, limits, trimmed tools, workspace |
| `tools.txt` | The six tools the profile exposes |
| `system.md` | Headless system prompt with task-agnostic working rules |
| `tasks.txt` | The 26 target tasks, from the first 44 |
| `run.sh` | One `harbor run` per task, one at a time |
| `host-gateway.compose.yaml` | Lets containers reach the host's vLLM on Linux |

## What the profile changes

All of it is opt-in: normal Agent8088 installs behave exactly as before. The
adapter sets `AGENT8088_DISPOSABLE_CONTAINER=1`, which is read from the
environment only. Under that flag:

- Files are written at the path the task names, instead of `artifacts/`.
- Non-catastrophic safety floors are relaxed: git checkout, reset and push;
  `curl | sh` installers; file names such as `count_tokens.py`. `rm -rf /`,
  `mkfs`, `dd` onto disks and fork bombs stay blocked.
- Before the final answer the agent is asked once to re-check every
  deliverable. The generate_tests nudge is skipped.
- `--prompt-file` skips the sandbox probe; the container is the sandbox.

General fixes that apply everywhere:

- A background server no longer hangs `execute_shell`.
- `execute_shell` takes an optional per-call `timeout`.
- `sleep`-polling commands are not suppressed as repeats.
- `max_tokens` is clamped so prompt plus output fits the context window.
- Compaction keeps a single-task instruction verbatim.
- `provider.<name>.extra_body` adds fields to each request.
- Qwen3 XML tool calls left in the message text are parsed.

## Running

On the GPU machine, with vLLM already serving `qwen3.8-27b` on port 8000:

```bash
uv tool install harbor
git clone https://github.com/harbor-framework/terminal-bench-2-1.git
uv build --wheel -o dist                    # in this repository
bench/terminal-bench/run.sh --tb terminal-bench-2-1 --wheel dist/agent8088-*.whl
```

Options:

| Option | Default | Notes |
|---|---|---|
| `--base-url` | `http://host.docker.internal:8000/v1` | The endpoint as seen from inside a container |
| `--model` | `qwen3.8-27b` | Must match `--served-model-name` |
| `--tasks` | `tasks.txt` | One task name per line |
| `--attempts` | `1` | Attempts per task |
| `--jobs-dir` | `jobs` | Where Harbor writes results |

Results are under `jobs/agent8088-<task>-<time>/`. The reward is in each
trial's `verifier/reward.txt`, and the agent's full log is
`agent/agent8088.txt`.

Runs are one at a time (`-n 1`) because vLLM runs with `--max-num-seqs 1`.

## Context window

`provider.vllm.context_window` must equal vLLM's `--max-model-len`, which is
32768 in the current launch command. The window is set by the vLLM server, not
by Terminal-Bench. To use a larger window, raise `--max-model-len` and both
`context_window` lines in `config.txt` together.

## Thinking

`config.txt` sends the vLLM server's own sampling defaults on every request:
thinking on, temperature 0.6, top_p 0.95, top_k 20, presence and frequency
penalty 0. A value in the request always overrides the server's default, so
each is sent explicitly rather than left to agent8088's own defaults (its
temperature default is 0.1). To compare with thinking off, set
`"enable_thinking": false` in `provider.vllm.extra_body`.

## Verified so far

- Unit tests cover every change above.
- The wheel installs with the adapter's `uv tool install` sequence, and
  `--prompt-file` is present.
- A headless end-to-end run of the installed wheel against a LAN
  Qwen3.8-27B (file tools only) produced the exact requested file. That run
  took 5 turns, 16 s and exited 0.
- A direct probe of that model with the profile's tool schemas returned
  native tool calls, and the thinking switch took effect per request.

**Not yet run:** a real `harbor run` on the GPU machine. There is no benchmark
result yet.

## Iteration log

Record every benchmark iteration here. Each fix must be general: explainable
without naming a task, a test or an expected output.

| Date | Tasks | Passed | Failure causes | General fix |
|---|---|---|---|---|
| | | | | |
