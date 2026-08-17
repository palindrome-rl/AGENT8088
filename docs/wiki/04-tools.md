# Tools

[← Wiki index](README.md)

21 built-in tools, registered from `src/agent8088/tools.txt`. The `mode` column
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
| `present_plan` | `plan` | `plan` | ✅ | Show a plan as markdown and ask the user to approve it (plan mode's exit point). |
| `execute_plan` | `plan` | `steps` | ✅ | Run an already-decided sequence of tool calls, verified step by step. |
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
down means `ddgs` serves and each search asks, because those queries do reach a
third party. Silent *and* external is the one combination this cannot produce.
If startup resolution is skipped entirely (an embedder calling the engine
directly), the unresolved value never matches the exemption, so it fails closed
to prompting.

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
| Follow-up fetches | After a search succeeds, an *unsolicited* `browse_page`, `curl`-style shell command, or fetch-shaped MCP call is refused |

An **approved plan** lifts the follow-up gate for the rest of that turn. A
plan-mode turn researches with a search and then carries out the approved steps in
the same turn, so the gate would otherwise refuse work the user had just said yes
to — and naming a tool is not how they said it, so the explicit-request escape
below cannot cover it. The exemption ends when the plan's turn does.

Every gate yields to an explicit request: give a URL, name a command, or name
an MCP tool and it runs. The gates only catch tools the model reached for on
its own.
