# Tools

[← Wiki index](README.md)

Built-in tools are registered from `src/agent8088/tools.txt`. The `mode` column
is what the permission layer gates on — see
[Permissions & Security](03-permissions-and-security.md).

## Describe one tool

Use `/tools read_text` or `/tool describe read_text` to inspect one tool without
executing it. With no name, `/tools` still lists all active tools. Names must match
the registry exactly; unknown names return an actionable error, not the full list.

The model-facing `describe_tool(tool_name)` uses the same live schema, including
required fields, optional fields, types, and native MCP parameter schemas. It is
available in read-only and plan-only modes and exposed by the MCP server. Web API
clients can request `GET /api/capabilities?tool_name=read_text`; omitting the query
parameter preserves the full report. Invalid names return HTTP 400, and unknown
names return HTTP 404. Describing a tool does not grant permission to execute it.

## Full inventory

| Tool | Mode | Args | readonly? | What it does |
|---|---|---|---|---|
| `read_text` | `read_text` | `filename`, `offset`*, `limit`* | ✅ | Read a file. Extracts `.docx`/`.xlsx`/`.pptx`/`.pdf` to text. Paginated. Refuses credential files. |
| `write_file` | `write_text` | `filename`, `content`*, `source_url`* | prompt | Write a file, or fetch and save binary/remote content from a URL. Path-zone + sensitive + shell-rc checked. |
| `edit_file` | `write_text` | `filename`, `old_string`, `new_string`* | prompt | Exact-replacement edit of one matching place; refuses an ambiguous match and writes nothing. Empty `new_string` deletes it. |
| `read_content` | `last_output` | `ref`, `offset`*, `length`* | ✅ | Read a window from a session-scoped content ref, without re-reading the source. |
| `document_read` | `read_text` | `filename`, `action`*, `cursor`*, `query`*, `version`* | ✅ | Cached document extraction with lossless cursors: `overview`, `search`, `read`, `process` (whole-document synthesis outside the turn loop). Never obeys instructions in documents. |
| `repository_read` | `read_text` | `source`, `action`*, `path`*, `query`*, `cursor`*, `snapshot_id`*, `include`*, `exclude`*, `revision`* | ✅ | Survey a local directory or an HTTPS github.com repo: `overview`, `search`, `read`. Remote defaults to root files only and needs permission. Untrusted evidence. |
| `repo_map` | `repomap` | `focus`*, `budget`*, `detail_level`* | ✅ | Outline the repository's most depended-on classes and functions, ranked by dependents. Definition lines only. |
| `generate_tests` | `autotest` | `filename`, `focus`* | prompt | Write and run tests for a source file via a sub-agent that may not change the code under test; the file is hashed and restored if it was. |
| `run_tests` | `shell` | `path`* | depends | Detect the project's test framework and run it. Prefer over `execute_shell`. |
| `execute_shell` | `shell` | `command` | safe list only | Run a shell command. |
| `calculate` | `python_eval` | `expression` | ✅ | Evaluate a maths expression. |
| `last_output` | `last_output` | `ref`*, `offset`*, `length`* | ✅ | Re-read the previous tool's output without re-running it; pass a truncation `ref` to read the elided part. |
| `memory_forget` | `memory` | `query` | prompt | Delete one specific memory, but only when exactly one clearly matches; several matches are listed back instead. |
| `describe_capabilities` | `introspect` | — | ✅ | Report own tools, MCP servers, skills, subagents, mode, sandbox, and active guardrails. |
| `describe_tool` | `introspect` | `tool_name` | ✅ | Describe one named tool's live schema — purpose, required/optional args and types. |
| `web_search` | `search` | `query` | no prompt with the default ddgs-only chain; prompts otherwise | Routes to the configured backend and falls back automatically. A pinned loopback or allowlisted private-LAN SearXNG can opt into no-prompt search with `web_search_no_prompt=1`. See [Web search backends](#web-search-backends). |
| `get_page_title` | `http_get` | `url` | prompt | Fetch just a page's `<title>`. |
| `browse_page` | `browser` | `url`, `task` | prompt | Headless browser — click, fill forms, navigate, and extract via natural-language instructions, not just read static text. |
| `create_document` | `write_text` | `filename`, `content` | prompt | Build a `.docx`/`.xlsx`/`.pptx` from plain lines. Same write gate as `write_file`. |
| `convert_document` | `write_text` | `filename`, `format` | prompt | Convert an existing Office document through LibreOffice. Same write gate as `write_file`. LibreOffice is opt-in; when it is missing the tool says to run `agent8088 --libreoffice-setup`. |
| `run_sandboxed` | `docker` | `code` | prompt | Run a Python snippet with native OS isolation and no network, falling back to Docker. |
| `schedule_task` | `cron` | `action`, `schedule`*, `task`* | prompt | Add/list/remove a scheduled run. |
| `spawn_subagent` | `subagent` | `agent_type`, `task` | prompt | Delegate to an isolated sub-agent. |
| `create_subagent` | `write_text` | `name`, `description`, `tools`, `max_turns`, `model`*, `prompt` | escalates | Create a custom sub-agent profile in `user_agents_dir`. |
| `present_plan` | `plan` | `plan` | ✅ | Show a plan as markdown and ask the user to approve it (plan mode's exit point). |
| `execute_plan` | `plan` | `steps` | ✅ | Run an already-decided sequence of tool calls, verified step by step. |
| `git_status` | `shell` | `repo`* | depends | `git status --short --branch`. |
| `git_diff` | `shell` | `repo`* | depends | Uncommitted working-tree diff only — never a branch's or commit's changes. Use `review_code` for those. |
| `git_log` | `shell` | `repo`* | depends | `git log --oneline -20`. |
| `git_init` | `shell` | `directory`* | prompt | Initialise a repository. Host-only: the sandbox cannot write refs. |
| `git_clone` | `shell` | `url`, `directory` | prompt | Clone a repo. |
| `git_branch` | `shell` | `name`, `repo`* | prompt | Create a branch without switching. Use this rather than `git branch` through `execute_shell`, which the sandbox refuses. |
| `git_checkout` | `shell` | `name`, `create`*, `repo`* | prompt | Switch branches (or create and switch with `create=true`). Refuses rather than discarding uncommitted work. |
| `git_commit` | `shell` | `message`, `repo`* | prompt | Stage all changes and commit. Only when the user asked. |
| `git_push` | `shell` | `remote`*, `branch`*, `set_upstream`*, `repo`* | **always asks** | Asks every time, in every mode including full-auto, and the approval covers only that exact remote and branch. A raw `git push` through `execute_shell` is refused at the always-on floor. |
| `git_create_pr` | `shell` | `title`, `body` | prompt | Open a PR via `gh`. |
| `git_remote` | `shell` | `action`, `name`*, `url`*, `repo`* | prompt | Inspect/configure remotes: `list`, `add`, `set-url`, `remove`. |
| `check_local_hardware` | `local_models` | — | ✅ | Probe RAM and NVIDIA VRAM and recommend which local Ollama models fit. Read-only, loopback only. |
| `list_local_models` | `local_models` | — | ✅ | List installed local Ollama models and which are loaded. Read-only, loopback only. |
| `pull_local_model` | `local_models` | `name` | prompt | Download a model into the local Ollama daemon. |
| `remove_local_model` | `local_models` | `name` | prompt | Delete a model from the local Ollama daemon's disk. |
| `add_mcp_server` | `mcp_manage` | `name`, `transport`, `command`*, `args`*, `url`*, `env`*, `bearer_token_env`*, `project`* | prompt | Configure and connect an MCP server (stdio or http), immediately reloading its tools. |
| `list_mcp_servers` | `mcp_manage` | — | ✅ | List configured MCP servers, connection state, and exposed tools. Read-only. |
| `remove_mcp_server` | `mcp_manage` | `name`, `project`* | prompt | Remove a configured MCP server. |
| `review_code` | `code_review` | `repo`*, `scope`*, `base`*, `head`*, `commit`*, `mode`*, `background`*, `pull_request`* | prompt | Review a diff and report findings with file, line and severity. See [`review_code`](#review_code). |
| `view_skill` | `skill` | `name`, `resource` | ✅ | Load one path-confined text resource from an enabled progressive skill. |
| `cli_anything_status` | `cli_anything` | — | ✅ | Report the isolated CLI-Anything runtime state. |
| `cli_anything_setup` | `cli_anything` | — | prompt | Install pinned CLI-Hub into its isolated environment. |
| `cli_anything_list` | `cli_anything` | — | prompt | List the official catalog as JSON. |
| `cli_anything_search` | `cli_anything` | `query` | prompt | Search the official CLI-Anything catalog. |
| `cli_anything_info` | `cli_anything` | `name` | prompt | Inspect one catalog entry. |
| `cli_anything_install` | `cli_anything` | `name` | prompt | Install one approved Python harness at the pinned upstream revision. |
| `cli_anything_update` | `cli_anything` | `name` | prompt | Reinstall one managed harness at the pinned upstream revision. |
| `cli_anything_uninstall` | `cli_anything` | `name` | prompt | Remove one managed harness. |
| `cli_anything_skill` | `cli_anything` | `name` | ✅ | Load an installed harness's packaged task guidance. |
| `cli_anything_run` | `cli_anything` | `name`, `arguments`, `cwd` | prompt | Run an installed harness with structured argv and no shell interpolation. |

`*` optional argument.

## Documents

Reading is automatic: point `read_text` at a `.docx`, `.xlsx`, `.pptx` or `.pdf`
and it comes back as text. `.docx` and `.pptx` are parsed with the standard
library; `.xlsx` uses openpyxl and `.pdf` uses pypdf. A scanned PDF with no text
layer says so rather than returning a blank-looking document. Files larger than
`max_document_bytes` (25 MB) are refused, and extracted text stops at 5 MB of
accumulated output no matter how far into the document that falls.

Long files arrive one page at a time with a header naming the true line count —
pass `offset` to continue, `limit` to change the page size (`read_page_lines`,
default 200). Short files are returned whole with no header. `document_read`
adds the stateful layer on top: `overview`, `search` at a lossless cursor,
`read`, and `process` for whole-document synthesis — the last runs every chunk
outside the main turn loop and returns page-referenced evidence, resumable by
repeating the same query.

Writing has two routes. The `documents` skill teaches the agent to write
`python-docx`/`openpyxl`/`python-pptx`/`reportlab` code and run it through
`execute_shell` — the flexible path, and the only one that produces PDFs or
edits an existing file. `create_document` is the deterministic fallback for when
generating that code is unreliable: it takes plain lines rather than code, but
only creates new `.docx`/`.xlsx`/`.pptx`.

`create_document` declares `mode=write_text` deliberately. Around a dozen places
key on that mode — the sensitive-file floor, write path zones, plan-only
blocking, plan-audit revert. Sharing the mode means the tool inherits every one
of them instead of needing a parallel set that could drift.

> `git_status`/`git_diff`/`git_log` are host tools (`host=1`), so they run on
> the host whatever the sandbox backend. In `readonly` they still ask, because a
> host-side git read can surface credential content; they are not treated as
> fixed commands since each takes an optional `repo` argument.

## `review_code`

One tool backs both the model's `review_code(...)` and the CLI's `/review`.
It is off until the engine is installed and `open_code_review_enabled=1`; it is
blocked outright in plan-only, and it needs host-shell inspection permission
before it reads the repository.

`scope` picks what is reviewed:

| `scope` | Covers |
|---|---|
| `workspace` (default) | Uncommitted work only — **not** the branch's commits. A workspace review that finds the branch ahead of its base says those commits were not reviewed. |
| `range` | A branch or range. Pass `head`; leave `base` out and the fork point is inferred. |
| `commit` | One commit, by SHA. |
| `pull_request` | A GitHub PR, passed as its complete `https://` URL, fetched locally with `gh` and reviewed as a range. A PR is **a separate grant** from a local repository: it reaches the network and lands someone else's code on disk, so it is asked for independently. |

`mode` is `native` (OpenCodeReview's own reviewer runs the pass, using this
session's provider through a transient subprocess environment), `delegated`
(scope, rules and the diff are returned for this agent to review in its own
context), or `auto` (the configured default — native when credentials exist,
else delegated). The result says which ran; an explicit mode never falls back
silently. The mode can also be set with `open_code_review_mode`.

What it does **not** do: propose or apply a fix. It reports findings with file,
line, severity and category, redacts them, and stores them so `/review --resume
<id>` can re-check each position later. Findings are evidence, not
authorisation — a line still has to be read before it is changed. This is the
code reviewer, not the document OCR in `ocr.py`; both upstreams use "OCR".

## Aliases

The model can call tools by natural names; they resolve to the canonical tool:

| Says | Runs |
|---|---|
| `bash`, `sh`, `shell`, `run` | `execute_shell` |
| `search`, `web`, `google` | `web_search` |
| `read`, `cat` | `read_text` |
| `write`, `create_file`, `writefile` | `write_file` |
| `calc`, `eval`, `math` | `calculate` |
| `last`, `prev_output` | `last_output` |

## Argument transforms

Some plausible-but-wrong shapes are rewritten rather than rejected — e.g.
`mkdir({path: "x"})` becomes `execute_shell({command: "mkdir x"})`. This is why
the agent recovers instead of looping when the model invents a tool that
*sounds* right.

## Tool modes explained

`mode` is the contract between a tool and the permission layer. Adding a tool to
`tools.txt` with an existing mode inherits that mode's gating automatically.

| Mode | Gated as |
|---|---|
| `read_text` | read — allowed in readonly |
| `write_text` | write — path zones, sensitive + shell-rc floor |
| `shell` | command classifier + sandbox |
| `http_get` / `http_post` | network + SSRF + content wrapping |
| `search` | network — prompt unless a no-prompt local SearXNG or the ddgs-only chain is in effect |
| `browser` | network + SSRF |
| `docker` | sandbox |
| `cron` | scheduled side effect — `list` alone is allowed in readonly and plan-only |
| `subagent` | recursion-depth guarded |
| `autotest` | delegates to the test-writer sub-agent, then verifies the source file was not modified |
| `python_eval` | pure computation — allowed in readonly |
| `last_output` | pure recall — allowed in readonly |
| `plan` | the plan-only entry point |
| `introspect` | self-report — allowed in **every** mode; touches no file, socket, or process |
| `memory` | one-match-only deletion of a stored memory |
| `repomap` | definition-line outline only — no permission gate, discloses less than the reads it saves |
| `local_models` | probe/list are read-only loopback calls; pull/remove take the host-shell gate |
| `mcp_manage` | list is read-only; add/remove take the host-shell gate |
| `code_review` | host-shell inspection gate, plus a *separate* grant for a PR checkout; blocked in plan-only |
| `mcp` | external MCP tool — see [MCP](07-mcp.md) |
| `skill` | path-confined local text loading for enabled progressive skills |
| `cli_anything` | action-specific catalog, package-change, or host-execution permission checks |

## Adding a tool

`tools.txt` is pipe-delimited:

```
name|description|mode=<mode>|args=a,b|timeout=25
```

HTTP tools take extra fields:

```
url=https://api.example.com/search
headers=Authorization: Bearer {my_api_key};;Content-Type: application/json
body={"q": "{query}"}
filter=.results[]        # jq expression applied to the response
extract=title            # or: return only the page <title>
```

Notes that save time:

- `{placeholders}` interpolate from config *and* tool args. `{query_q}` is the
  URL-encoded variant of `{query}`.
- Headers are split on `;;`, then on the first `:` — so a `User-Agent`
  containing semicolons works fine.
- An unresolved `{placeholder}` produces a message naming the missing key,
  rather than a confusing SSRF error.
- Everything stays behind the SSRF guard, which is exactly why HTTP is a *mode*
  rather than something you'd shell out to `curl` for.

### Disabling a built-in

There is **no `disabled_tools` config key** — the loader has no such filter, so
setting one has no effect. To drop a tool, comment out its line (the parser
skips blank lines and `#`):

```
# browse_page|Load a user-supplied web page…
```

To do it without editing the installed package, copy `tools.txt`, remove the
line, and point config at your copy:

```ini
tools_file=~/.agent8088/tools.txt
```

Either way, confirm it is gone with `/tools` — the registry is what the model is
offered, so a tool absent there cannot be called at all.

## `describe_capabilities`

Ask the agent what it can do and it answers from fact, not from its own reading
of the prompt:

> **you:** what tools and MCP servers do you have?
> **agent:** *(calls `describe_capabilities`)* …

The report is generated from live state — `TOOL_SPECS` grouped by access mode,
`MCP_RUNTIME.statuses` with per-server connection state and tool lists, installed
skills, configured subagents, the resolved sandbox backend, every limit including
the ones **not** set, and the always-on floor. Because it is generated rather
than hand-maintained, it cannot drift from what the agent actually has.

Available on every surface, all from the same function, so a human and the model
never get different answers:

| Surface | How |
|---|---|
| Model | the `describe_capabilities` tool |
| CLI | `/capabilities` |
| Gateway chat | `/capabilities` |
| MCP client | exposed in the default non-mutating server surface |

It is permitted in **every** permission mode, including `readonly` and
`plan-only`: an agent that cannot say what it can do is least useful exactly when
it is most restricted. Safe to allow because it opens no file, makes no request,
and starts no process — and its output goes through the same secret redaction as
any other tool result, with no system-prompt text in it.

## Inspecting tools at runtime

```
/tools                        # list all with mode, args, description
/capabilities                 # tools + MCP + skills + limits + guardrails
/tool read_text {"filename": "README.md"}   # invoke one directly
```


## Web search backends

`web_search` is one tool with four interchangeable backends, chosen by
configuration rather than by the model picking a per-vendor tool:

| Backend | Role | Requires |
|---|---|---|
| `searxng` | **default** | Docker (`/search setup` provisions it) or an instance URL |
| `ddgs` | **fallback** | nothing — ships with agent8088 |
| `tavily` | optional — **first priority once its key is set** | `TAVILY_API_KEY` in the `.env` store |
| `exa` | optional — **priority once its key is set**, behind `tavily` | `EXA_API_KEY` in the `.env` store |

`web_search_provider` decides which one serves:

- **`auto` (the shipped default)** — at startup, probe and pick the
  highest-priority backend that can actually serve: a keyed `tavily`/`exa`
  first, else `searxng` **if it answers**, else `ddgs`. The winner is then
  pinned for the session. SearXNG has to pass a real liveness probe because a
  pin has no fallback, so pinning a stopped instance would mean no web search.
- **An explicit name** — pins exactly that backend. No auto-selection, no
  fallback. `/search use <name>` writes it.

Adding an API key is the signal to prefer that backend, so a configured
`tavily` or `exa` outranks both keyless backends; with both keys set, `tavily`
goes first. An optional backend whose key is absent stays out entirely.

**`auto` pins rather than staying dynamic, and that is deliberate.**
`web_search_no_prompt=1` only takes effect while a *local* SearXNG is the
effective pin, because approval-free search is safe only when the query cannot
leave your network. So under `auto`: SearXNG up means silent searches; SearXNG
down means `ddgs` serves. A chain that is `ddgs` alone (the default on a fresh
install, with no SearXNG and no keyed backend) also runs without a prompt, in
every mode, even though those queries do reach a third party. The query guards
and the outbound-secret check still run first. Configure SearXNG, Tavily or Exa
and searches that can leave your network ask again. If startup resolution is
skipped entirely (an embedder calling the engine directly), the unresolved
value never matches the local-SearXNG exemption.

If the chosen backend fails at call time — instance stopped, rate limited — the
next available one serves the request, so a broken primary does not mean "no web
search". The result always names which backend served it, so a silent fallback
is visible.

Because `ddgs` needs no key, no hosting, and no setup, web search works on a
fresh install. Run `/search status` for the live chain, `/search doctor` to
diagnose, and `/search use <backend>` to pin one.

## Pointing web search at a SearXNG

`search_base_url` ships **unset**. Nothing is assumed about your network, so a
fresh install searches through the keyless `ddgs` fallback until you choose an
endpoint. There are three ways to set one.

### 1. Provision a local instance (recommended)

Needs Docker. From the REPL:

```
/search setup
```

That writes a `settings.yml` with JSON output enabled and a random
`secret_key`, starts the container on `127.0.0.1:8888`, waits for the JSON API
to answer, then saves `search_base_url` and allowlists the host for you. Nothing
else to do. If the container never answers, nothing is saved — a backend that
cannot serve must not be recorded, or the chain would try it first on every
search.

To move it off port 8888:

```
searxng_host_port=8888
```

The **host** is not configurable. SearXNG's JSON API has no authentication, so
the container is always published to `127.0.0.1` only — binding it to `0.0.0.0`
would put an open search proxy on your network.

`/search stop` removes the container.

### 2. Point at an instance you already run

Set the endpoint by hand in `config.txt`. It must end at `search?q=` with no
placeholder — the query is appended for you:

```
# on this machine
search_base_url=http://127.0.0.1:8888/search?q=

# elsewhere on your LAN — the host must also be allowlisted
search_base_url=http://192.168.1.10:8888/search?q=
ssrf_allow_hosts=127.0.0.1,localhost,192.168.1.10:8888
```

A private address the agent has not been told about is blocked as internal, which
is why the LAN case needs the second line. Add the port when the instance runs on
one: `ssrf_allow_hosts` entries match `host` or `host:port`.

Your instance must have JSON output enabled — upstream SearXNG **disables it by
default**, and without it every search fails with a parse error. In its
`settings.yml`:

```yaml
search:
  formats:
    - html
    - json
```

### 3. Point at a public instance

`https://` is **required** for a public host. Plaintext `http://` is accepted
only for loopback and private addresses, so queries never cross the internet in
the clear:

```
search_base_url=https://searx.example.org/search?q=
```

Most public instances rate-limit or block API clients, so expect HTTP 429 and
keep `ddgs` available as the fallback. Approval-free search is never granted to
a public host, no matter what `web_search_no_prompt` says.

### Verify it

```
/search status    # which backend is pinned right now, and the whole chain
/search doctor    # container state, endpoint, SSRF coverage, JSON check
```

`/search doctor` reports `search_base_url` as `not set (using fallback)` when no
endpoint is configured, which is the normal state on a fresh install.

### Using an API-key backend instead

If you would rather not host anything, add a key to the `.env` store next to
`config.txt` and that backend joins the chain automatically, outranking both
keyless ones:

```
TAVILY_API_KEY=...   # agent-optimized results with citations
EXA_API_KEY=...      # semantic/neural search
```

Keys never go in `config.txt`. `/search setup` prompts for them if you pick one
of those backends.

## How the agent chooses a tool

Tool choice is enforced in three places, each doing only what it is good at.

**The prompt** carries the judgement calls: use the smallest tool that answers
the request, never call one for text you already have (summarizing,
translating, reasoning about readable code, writing), treat MCP tools as
belonging to the system they wrap, and always follow an explicit instruction
over any of these preferences.

**Runtime context** gives the model the current date. Without it there is only
a training cutoff, so "the next election" means whatever was next during
training and an old page reads as current.

**The engine** enforces what a prompt cannot be trusted with:

| Behaviour | What happens |
|---|---|
| Date-qualified queries | A query meaning "as of now" with no year of its own gets the current year appended — or the month, for "today"/"this week". Controlled by `search_date_augmentation` |
| Result dating | Results are stamped with their retrieval date so the model can spot a stale one |
| Repeat searches | A reworded or reordered repeat is answered from the first search's results instead of re-running. A failed or empty search stays retryable |
| Follow-up fetches | After a search succeeds, an *unsolicited* `get_page_title`, `curl`-style shell command, or fetch-shaped MCP call is refused. `browse_page` requires a user-supplied page URL or an explicit browser/interactive website request; a search-result URL or a request to visit a physical attraction does not authorize a browser session. |

An **approved plan** lifts the follow-up gate for the rest of that turn. A
plan-mode turn researches with a search and then carries out the approved steps in
the same turn, so the gate would otherwise refuse work the user had just said yes
to — and naming a tool is not how they said it, so the explicit-request escape
below cannot cover it. The exemption ends when the plan's turn does.

Every gate yields to an explicit request: give a URL, name a command, or name
an MCP tool and it runs. The gates only catch tools the model reached for on
its own.
