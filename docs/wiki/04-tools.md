# Tools

[← Wiki index](README.md)

22 built-in tools, registered from `src/agent8088/tools.txt`. The `mode` column
is what the permission layer gates on — see
[Permissions & Security](03-permissions-and-security.md).

## Full inventory

| Tool | Mode | Args | readonly? | What it does |
|---|---|---|---|---|
| `read_text` | `read_text` | `filename` | ✅ | Read a file. Refuses credential files. |
| `write_file` | `write_text` | `filename`, `content` | prompt | Write a file. Path-zone + sensitive + shell-rc checked. |
| `execute_shell` | `shell` | `command` | safe list only | Run a shell command. |
| `calculate` | `python_eval` | `expression` | ✅ | Evaluate a maths expression. |
| `last_output` | `last_output` | — | ✅ | Re-read the previous tool's output without re-running it. |
| `describe_capabilities` | `introspect` | — | ✅ | Report own tools, MCP servers, skills, subagents, mode, sandbox, and active guardrails. |
| `web_search` | `search` | `query` | prompt by default | Routes to the configured backend and falls back automatically. A pinned loopback or allowlisted private-LAN SearXNG can opt into no-prompt search with `web_search_no_prompt=1`. See [Web search backends](#web-search-backends). |
| `get_page_title` | `http_get` | `url` | prompt | Fetch just a page's `<title>`. |
| `browse_page` | `browser` | `url` | prompt | Headless browser — renders JS that curl can't. |
| `run_sandboxed` | `docker` | `code` | prompt | Run code in the sandbox. |
| `schedule_task` | `cron` | `action`, `schedule`, `task` | prompt | Add/list/remove a scheduled run. |
| `spawn_subagent` | `subagent` | `agent_type`, `task` | prompt | Delegate to an isolated sub-agent. |
| `execute_plan` | `plan` | `steps` | ✅ | Run a multi-step plan (the plan-only path). |
| `git_status` | `shell` | — | depends | `git status`. |
| `git_diff` | `shell` | — | depends | `git diff`. |
| `git_log` | `shell` | — | depends | `git log`. |
| `git_clone` | `shell` | `url`, `directory` | prompt | Clone a repo. |
| `git_commit` | `shell` | `message` | prompt | Commit staged changes. |
| `git_push` | `shell` | — | **blocked** | Refused at the always-on floor. |
| `git_create_pr` | `shell` | `title`, `body` | prompt | Open a PR via `gh`. |

> `git_status`/`git_diff`/`git_log` depend on the sandbox backend: allowed
> without a prompt under the native sandbox, escalated under `local`, because
> reading a repo unsandboxed can surface credential content.

## Aliases

The model can call tools by natural names; they resolve to the canonical tool:

| Says | Runs |
|---|---|
| `bash`, `sh`, `shell`, `run` | `execute_shell` |
| `search`, `web`, `google` | `web_search` |
| `read`, `cat` | `read_text` |
| `write`, `create_file` | `write_file` |
| `calc`, `eval`, `math` | `calculate` |

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
| `browser` | network + SSRF |
| `docker` | sandbox |
| `cron` | scheduled side effect |
| `subagent` | recursion-depth guarded |
| `python_eval` | pure computation — allowed in readonly |
| `last_output` | pure recall — allowed in readonly |
| `plan` | the plan-only entry point |
| `introspect` | self-report — allowed in **every** mode; touches no file, socket, or process |
| `mcp` | external MCP tool — see [MCP](07-mcp.md) |

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

Disable a built-in without editing the file:

```ini
disabled_tools=browse_page
```

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
| `tavily` | optional | `TAVILY_API_KEY` in the `.env` store |
| `exa` | optional | `EXA_API_KEY` in the `.env` store |
| `ddgs` | **fallback** | nothing — ships with agent8088 |

Selection order is `web_search_provider` (explicit pin), then the first
available of `searxng -> tavily -> exa -> ddgs`. If the chosen backend fails at
call time — instance stopped, rate limited — the next available one serves the
request, so a broken primary does not mean "no web search". The result always
names which backend served it, so a silent fallback is visible.

Because `ddgs` needs no key, no hosting, and no setup, web search works on a
fresh install. Run `/search status` for the live chain, `/search doctor` to
diagnose, and `/search use <backend>` to pin one.
