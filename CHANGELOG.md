# Agent8088 Changelog

All notable changes to the Agent8088 project, organized by feature area.

---

## Permission Layer

### readonly → edit Escalation (commits `2f2a3e6`, `e720b88`, `0a7339e`)
- Added `PERMISSION_MODE` global (defaults to `"readonly"` at session start)
- `check_permission()` gates write/shell operations — blocks in readonly, allows in edit
- `run_tool()` returns `ESCALATION_REQUEST:edit:change_type:paths:reason` when blocked
- `grant_escalation()` transitions to edit mode for the session

### Per-Action Prompting (commit `0ad31ca`)
- Changed `grant_escalation()` from session-wide edit mode to one-shot grant
- `_one_shot_grant` flag allows exactly ONE blocked tool to run, then reverts to readonly
- Every new write/folder/system command prompts the user separately
- `--edit` flag still gives permanent edit mode (explicit override)

### Escalation Retry (commit `810bb0f`)
- After user approves, injects `"Permission granted. Retry the tool call that was blocked."` into messages
- After user declines, injects denial message so model informs the user
- `seen.discard(sig)` fix: permission-blocked calls are not cached as "already ran" (commit `129ee7a`)

### Allow/Decline Prompt (commits `810bb0f`, `ffa57e1`, `e935887`)
- Rich `Prompt.ask()` with Allow/Decline choices → simplified to `y/n` (case-insensitive)
- `live.stop()` before prompt, `live.start()` after — fixes input capture inside Live display
- Removed `request_permission_escalation` tool entirely — engine gate generates accurate paths

---

## Tool Alias Resolution (commit `26382a3`)

### Problem
Model (Ornith-35B) emitted natural tool names like `bash` instead of canonical `execute_shell` from `tools.txt`. `find_tool_calls()` did exact match → call silently dropped → raw `✿FUNCTION✿` text displayed as answer.

### Fix
- `TOOL_ALIASES` map: `bash`→`execute_shell`, `search`→`web_search`, `read`→`read_text`, `write`→`write_file`, `calc`→`calculate`, etc.
- `_resolve_tool_name()` resolves aliases before `TOOL_NAMES` check
- All 5 match sites in `find_tool_calls()` use the resolver

## Tool Arg Transforms (commit `a1552ee`)

### Problem
Model called `mkdir({"path": "testing"})` instead of `execute_shell({"command": "mkdir testing"})`. Alias map only resolved the name, not the args format.

### Fix
- `TOOL_ARG_TRANSFORMS` dict of lambdas: `mkdir`→`{"command": "mkdir {path}"}`, `rm`→`{"command": "rm {path}"}`, `cp`→`{"command": "cp {src} {dst}"}`, etc.
- `_resolve_tool_args()` applies the transform when alias resolves to `execute_shell`
- Covers: mkdir, rmdir, touch, rm, cp, mv, cat, echo, ls, grep, find, pwd, chmod, curl, wget, git, pip, python, node

---

## Security Layers (commit `a6ee527`)

### Layer 1: Sensitive File Read Protection
- Hardcoded blocklist: `.env`, `config.txt`, `configb.txt`, `id_rsa`, `.ssh/`, `*.pem`, `*.key`, `*_KEY*`, `*_SECRET*`, `*_TOKEN*`, `*_PASSWORD*`
- `_is_sensitive_path()` checks before `read_text` runs — returns `"Access to sensitive file denied"`
- Config override: `allowed_sensitive_files=.env,secrets.yaml` in `config.txt`

### Layer 2: Network Access Control
- `web_search` and `get_page_title` prompt the user (y/n) on **every** request
- Removed `http_get` from readonly-safe mode list in `check_permission()`
- Shows the full URL being requested in the escalation panel
- One-shot grant: each approval covers one network request only

### Layer 3: Path-Based Write Restrictions (Three-Tier Zones)
- `no_prompt_paths=/tmp` — writes here run without a prompt (auto-approved)
- `prompt_paths=.` — writes to project dir show the y/n escalation
- `blocked_paths=/etc,/home` — writes here are **always blocked**, even in edit mode
- Pre-resolution check for blocked paths (before `resolve_user_path()` ValueError)
- Shell mutating commands (`mkdir`, `rm`, `mv`, `cp`, `touch`) also check blocked paths

---

## Cross-Platform Support (commit `79cdfcb`)

### Windows Shell
- `_exec_shell_command()`: uses `cmd.exe` on Windows, `/bin/bash` on Linux
- `READONLY_SAFE_COMMANDS`: added Windows equivalents (`dir`, `type`, `findstr`, `hostname`, `ver`, `vol`, `tasklist`, `systeminfo`)
- Added cross-platform commands (`python`, `pip`, `node`, `npm`, `curl`, `wget`)

### Cross-Platform get_page_title
- Replaced `grep -oP` (Linux-only Perl regex) with Python one-liner using `urllib` + `re`
- Works on both Windows and Linux

### readline Import
- Made `readline` import safe with `try/except` (Unix-only module)

---

## Config Fixes (commit `e509c32`)

### Relative Paths
- Commented out hardcoded `/home/amir/projects/agent8088/` paths in `config.txt`
- Engine falls back to `APP_DIR` defaults (script's own directory)
- `allowed_paths` uses `.` (resolved to `PROJECT_ROOT` at runtime) instead of hardcoded path
- Fixes: "Tools: 0 loaded" banner, tool calls silently dropped

### Three-Tier Path Zones Config
- `no_prompt_paths=/tmp` — auto-approved writes
- `prompt_paths=.` — y/n prompt for writes
- `blocked_paths` — always blocked (commented out by default)

---

## Rich CLI UI (commit `41f6d91`)

### New File: `agent8088_cli.py`
- Hermes-style interactive interface importing the real `agent8088` engine as a module
- Live token streaming (`on_token` callback, `stream=True`)
- ESC-to-interrupt (`EscListener` + `AgentInterrupted` exception)
- Rich panels, tables, diffs for tool output
- Slash commands: `/tools`, `/tool`, `/plan`, `/raw`, `/model`, `/config`, `/system`, `/history`, `/trace`, `/temp`, `/maxturns`, `/save`, `/clear`
- `--edit` flag for permanent edit mode (no per-action prompts)
- Context-window progress hint (`% ctx` in prompt)

### Engine APIs Added for UI
- `on_token` streaming in `create_completion()` (backward-compatible)
- `interrupt_check` in `run_agent()` (raises `AgentInterrupted`)
- `_last_write_diff` + `_make_diff()` for rich diff display
- `CONTEXT_WINDOW` global (configurable via `config.txt`)
- `on_step` callback in `_exec_plan()` for live plan checklist

---

## Repo Restructure (commit `41f6d91`)

### Clean Folder Structure
- `configs/` — model-config variants (`reality7b_config_colossus.py`, `reality7b_config_ollama.py`)
- `scripts/` — one-off repo ops (`configure.sh`, `push-to-github.sh`, `verify-push.sh`)
- `research/` — non-runtime pipeline (`skillopt.py`, `run_benchmark.py`, `data_cleanup/`, `vast-training/`, `paper/`)
- `docs/` — design specs and plans
- `skills/` — 20 agent skill YAMLs (unchanged)

### Removed (24 files + 2 dirs)
- 16 toy algorithm scripts (factorial.py, fibonacci.py, gcd.py, etc.)
- 2 unrelated web projects (`amirweb/`, `projects/ahweb3/`)
- 2 one-time deployment docs (`DEPLOYMENT_STATUS.md`, `GITHUB_SETUP_INSTRUCTIONS.md`)

### Path Fixes in Moved Files
- `research/skillopt.py`: `config.txt`/`system.md` resolve to `APP_DIR.parent` (repo root)
- `research/run_benchmark.py`: hardcoded `/home/amir/projects/agent8088` replaced with `Path(__file__).resolve().parent.parent`

---

## Backward Compatibility (commit `0efdc8a`)
- Old `./agent8088` REPL defaults to edit mode (no behavior change)
- `run_benchmark.py` sets `AGENT8088_PERMISSION=edit` via `os.environ.setdefault()`
- `AGENT8088_PERMISSION=edit` env var works for any caller
- All new engine params default to `None` — existing callers unaffected

---

## Graphify Integration
- `graphify opencode install` — AGENTS.md always-on section + `.opencode/plugins/graphify.js`
- Graph refreshed after every code change (`graphify update .`)
- 307 nodes, 375 edges, 32 communities (latest)
- Gemini-powered semantic extraction (38,690 + 35,064 tokens across 2 runs)