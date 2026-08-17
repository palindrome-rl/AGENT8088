# Troubleshooting

[← Wiki index](README.md)

Symptom-first. Run `/doctor` in the REPL for an automated health check.

## Install & startup

### `No module named pytest` / `No module named slack_bolt`

The optional extras aren't installed. They're separate on purpose so a plain CLI
install stays small:

```sh
pip install -e ".[gateway,dev]"
```

If a venv was rebuilt (e.g. `uv sync`), extras are dropped and need reinstalling.

### ~19 gateway test failures that look catastrophic

`ModuleNotFoundError` at import in `tests/gateway/platforms/`. It's the missing
`gateway` extra, not broken code — those tests error instead of skipping. Install
the extra and re-run.

### `[agent8088] Migrated N keys to .../.env` appeared unexpectedly

Expected and harmless — the one-time key migration. Keys moved from
`config.txt` into `.env` (mode `0600`), with `api_key_env` pointers left behind.
It's idempotent and lossless.

It runs on *any* invocation, including `--help`, so it fires if you run the CLI
without an isolated `HOME` during testing. See
[Testing](12-testing-and-verification.md#isolation-rules-for-anything-you-write).

### `Config not found` from a setup wizard

`--gateway-setup` and friends need a base config first:

```sh
agent8088 --setup
```

## Model & providers

### `'anthropic' is not a known provider`

There is no `anthropic` built-in provider — the 12 built-ins do not include one.
Reach Claude through OpenRouter or a custom `api_mode=litellm` profile; see
[Model Providers](05-model-providers.md#reaching-anthropic--claude).

### Wrong API key being used

Resolution order is `.env` → explicit `api_key` in config → `os.environ`.
`os.environ` is **last**, so a shell export can't override configured settings.
If you expected the env var to win, that's why.

Check which sources exist:

```sh
grep -n "api_key" ~/.agent8088/config.txt        # pointers, not secrets
grep -o "^[A-Z_]*=" ~/.agent8088/.env            # names only
```

### `not configured: set <name>_api_key in config.txt`

A tool URL/header has an unresolved `{placeholder}`. The message names the exact
missing key — add it to config, or use a different search tool.

### Provider silently missing from `/models`

A provider needs **both** `base_url` and `model` to load; incomplete profiles
are dropped rather than half-registered. `api_mode=litellm` is the one exception
(no base URL needed).

### Fallback chain not firing

Only **retryable** errors trigger it: HTTP 429, 503, connection errors. A 401 or
400 is deterministic — retrying elsewhere would just waste a call.

## Permissions

### Every write asks for approval

That's `readonly`, the default. Options: approve per action, `/mode full-auto`,
or add the directory to `no_prompt_paths`.

### `Writing to sensitive file denied` and I meant it

Hitting the always-on floor. Credential files (`.env`, `.ssh`, `*.pem`,
`*_TOKEN*`) and shell startup files (`.zshrc`, `.bashrc`, `.profile`) are refused
in **every** mode, including full-auto, and no escalation grant unlocks them.

For credential files there's an escape hatch:

```ini
allowed_sensitive_files=.env.example
```

Shell startup files have no override by design — writing one is code execution on
your next shell launch. Edit it yourself.

### `plan mode — nothing is written or run until the user approves a plan`

Working as intended. You are in plan mode. The agent will read whatever it needs,
then call `present_plan` with the plan as markdown; approve it and the mode
changes so the plan runs. To leave without a plan, `/mode full-auto` or
`/mode readonly`.

### The agent described a plan but nothing happened

Look for `Still in plan mode — no plan was approved, so nothing above was written
or run.` A plan the model only wrote out in prose is not a plan it ran, and that
line is how you tell the two apart. Reply to have it revise and actually call
`present_plan`.

### full-auto still won't `git push`

Correct. Destructive git (`push`, `reset --hard`, `branch -D`) is on the
always-on floor. Run it yourself.

### Approving once didn't stick

An escalation grant covers **exactly one** action and is then consumed. That's
deliberate — approving one write must not silently become full-auto.

## Network & search

### `Blocked: '127.0.0.1' resolves to internal address`

SSRF protection. To reach a genuinely local service, allowlist that host only:

```ini
ssrf_allow_hosts=127.0.0.1,localhost
```

Prefer this over `ssrf_allow_private=1`, which opens the whole private network.

### `web_search` returns nothing / connection refused

Run `/search doctor`. It reports the container state, the active backend chain,
whether `ddgs` is importable, and whether a configured host is actually covered
by `ssrf_allow_hosts`.

Web search should not fail outright: the keyless `ddgs` backend ships with
agent8088, so an unreachable SearXNG falls through to it. If you get an error
instead of results, the chain is pinned (`web_search_provider=`) or a guard
denied the request — a guard denial deliberately does **not** fall through.

### SearXNG returns HTML instead of JSON

SearXNG ships with **JSON output disabled**. `/search setup` writes this for
you; a hand-rolled instance needs it in `settings.yml`:

```yaml
search:
  formats:
    - html
    - json
```

### SearXNG returns HTTP 403 or 429

The bot limiter is on. For a loopback instance used as an API, turn it off:

```yaml
server:
  limiter: false
```

### `ddgs is rate limited`

DuckDuckGo throttled the request. `ddgs` scrapes rather than using an API, so it
is the least reliable backend under sustained use — that is why it is last in
the chain. Provision SearXNG (`/search setup`) or add a `TAVILY_API_KEY` /
`EXA_API_KEY` for heavier use.

### A remote SearXNG is refused

Plaintext `http://` is only accepted for loopback and private hosts; a public
instance must use `https://`. Its host must also be in `ssrf_allow_hosts` if it
resolves to an internal address. See
[Pointing web search at a SearXNG](04-tools.md#pointing-web-search-at-a-searxng)
for the exact settings per case.

### `search_base_url` shows as "not set (using fallback)"

That is the shipped default, not a fault: no endpoint is assumed for you, so
`ddgs` serves until you configure one. Run `/search setup` to provision a local
instance, or set `search_base_url` by hand — see
[Pointing web search at a SearXNG](04-tools.md#pointing-web-search-at-a-searxng).

### Port 8888 is already in use

Set `searxng_host_port` in `config.txt` and re-run `/search setup`. The bind host
stays `127.0.0.1` either way — the unauthenticated JSON API is never published to
the network.

### Search worked, then stopped after a redirect

Redirect targets are re-checked against SSRF. A public URL that 302s to a
private address is blocked at the redirect — by design.

## MCP

### An MCP server shows `error` in `/mcp`

`/mcp` prints the reason per server. Common causes: `command` not on `PATH`;
both or neither of `command`/`url` set; `bearer_token_env` naming an unset
variable; a server name with illegal characters. One bad server doesn't stop the
others.

### MCP tool needs approval every time

Tools without the server's `readOnlyHint` annotation are treated as mutating and
gated normally. That's the server's annotation to fix, not a config setting.

### `write_file` missing over `--mcp-serve`

Intentional — the MCP tool surface is read-only by default because MCP has no
approval channel. Opt in:

```ini
mcp_server_allow_writes=1
```

Narrow `allowed_paths` and set `blocked_paths` first; writes are unattended.

### MCP HTTP server reachable by others

There's **no authentication** on the HTTP transport. It binds `127.0.0.1` by
default. Remote binds are intentionally rejected because the bundled HTTP
transport has no authentication.

## Gateway

### Bot silently ignores someone

Almost always the allowlist. Empty means nobody (fail-closed). Check the log for
`disallowed user dropped: <id> (<platform>)`.

If you see `allowing <id> on discord, but it is configured under
slack_allowed_users` — the id is on the wrong config line. It still works, but
move it.

### Slack bot answers nothing in channels

By design: it responds only to **DMs and @mentions**, not all channel traffic.
Confirm the `app_mention` event subscription and the Messages tab are enabled.

### WhatsApp: "failed to find key" after re-pairing

Stale app-state-sync keys. Re-pairing wipes the **entire** session directory for
this reason — if you restored a partial backup, delete
`whatsapp_session_dir` and pair again.

### Discord bot sees no message text

The **Message Content** intent isn't enabled in the developer portal. It's
required.

### Gateway approvals never arrive

Check `gateway_permission_mode`. Under `edit` there are no prompts at all.

## Sandbox

### `REAL native sandbox — missing: sandbox-runtime`

Not installed:

```sh
agent8088 --sandbox-setup
```

Needs Node.js 20.11+; Linux also needs `bubblewrap`, `socat`, `ripgrep`.

### Asked to run a command "without isolation"

Neither backend is available. Install the native runtime or Docker; `local`
means no isolation at all.

### Sandboxed command can't reach the network

Correct by default. Allowlist what it needs:

```ini
sandbox_allowed_domains=pypi.org,api.example.com
```

Note this is separate from `ssrf_allow_hosts`, which governs the HTTP *tools*.

## Development

### A test passes alone but fails in the suite (or vice versa)

`tests/test_cli_setup.py` has known cross-test fixture dependence — 3 cases fail
in isolation but pass in the full suite. Pre-existing; run the full suite to
judge.

### Duplicate function silently ignored

Python keeps only the last definition — no error. Run:

```sh
python scripts/check_duplicate_defs.py
```

Don't rely on ruff `F811`; it has verifiably missed this in this codebase.

### CI checks failing instantly

GitHub Actions is blocked by a billing issue on this account, so jobs fail in
~3s without starting. Not a code problem — run the suites locally.

## Still stuck

```
/doctor      # environment health
/config      # active config + path
/status      # model, mode, tools, skills
/trace on    # capture full JSON trace, then /save trace.json
```
