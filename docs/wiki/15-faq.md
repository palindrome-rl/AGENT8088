# FAQ

[← Wiki index](README.md)

Short answers to the questions that come up most. Each links to the page with
the full detail.

## Using it

### What can this agent actually do right now?

Ask it, rather than reading a list that can go stale:

```
/capabilities
```

That reports the live permission mode, sandbox backend, connected MCP servers,
installed skills, every limit — including the ones **not** set — and the
always-on floor. It is generated from the running configuration, so it cannot
drift. The model answers the same question with the `describe_capabilities`
tool, so you and it never get different answers. See [Tools](04-tools.md#describe_capabilities).

### Why did it refuse to write a file / run a command / search the web?

`readonly` is the default mode. Reads are allowed; writes, shell, network,
scheduling and browser actions each need a one-time approval.

Approving grants **exactly one** blocked call. It is not a mode change — the
next write asks again. See [Permissions & Security](03-permissions-and-security.md).

### It refused even after I approved. Why?

You hit the always-on floor, which no mode and no approval unlocks:

- reading or writing credential files (`.env`, `.ssh`, `*.pem`, `*_TOKEN*`, …)
- writing shell startup files (`.zshrc`, `.bashrc`, `.profile`, …)
- `git push`, `git reset --hard`, `git branch -D`
- sending a configured secret to an external service
- asking the agent for its own system prompt

These are refused in `full-auto` too. That is deliberate — see
[the always-on floor](03-permissions-and-security.md#the-always-on-floor).

### Why does `whoami` run instantly but `echo hi > f.txt` needs approval?

25 inspection-only commands are on a readonly-safe list. Anything that can
change state is classified as a mutation, and the classifier looks *through*
`sh -c`, `&&` and `;` rather than checking the first word. So
`sh -c "ls && rm -rf ."` is caught. Full list in
[Permissions & Security](03-permissions-and-security.md#what-readonly-actually-allows).

### How do I turn off a tool?

There is **no `disabled_tools` config key**. Comment out the tool's line in
`tools.txt`, or point `tools_file` at an edited copy:

```ini
tools_file=~/.agent8088/tools.txt
```

Confirm with `/tools`. See [Disabling a built-in](04-tools.md#disabling-a-built-in).

### Can I use Claude / Anthropic models?

Yes, but not through a built-in provider — there is no `anthropic` entry among
the 12. Use OpenRouter, or a custom profile with `api_mode=litellm`. See
[Model Providers](05-model-providers.md#reaching-anthropic--claude).

## When something looks broken

### The agent says a sandbox is required and refuses to run anything

Neither the native sandbox nor Docker is available. Agent8088 has no
unsandboxed fallback — it refuses rather than quietly running commands
unprotected.

```sh
agent8088 --sandbox-setup     # or: start Docker
```

See [No unsandboxed fallback](06-sandboxing.md#no-unsandboxed-fallback).

### On Windows, everything fails with a `whoami` or SID error

Run from **PowerShell, not Git Bash**. Under MSYS, `whoami` shadows Windows'
`whoami.exe`, so the private-file protection cannot parse a SID.

### It can't reach my local service

The SSRF guard refuses loopback, private ranges and link-local addresses,
including the cloud metadata endpoint. Allowlist the one host you need:

```ini
ssrf_allow_hosts=127.0.0.1,localhost
```

Prefer that over `ssrf_allow_private=1`, which opens the whole private network.
See [SSRF protection](03-permissions-and-security.md#ssrf-protection).

### The email bot connects but never replies

`email_verify_sender` is on by default and **fails closed**: a message with no
`Authentication-Results` header is rejected outright. If your mail server does
not add that header, every message is dropped silently. Unauthorized mail is
discarded without a reply either way, so silence is the expected symptom rather
than an error. See [Email](08-messaging-gateway.md#email).

### Search stopped working after I set `allowed_domains`

`allowed_domains` makes those the *only* reachable public hosts. Include the
host from `search_base_url`, or search will fail. See
[Egress domain policy](03-permissions-and-security.md#egress-domain-policy).

### It keeps proposing something I already refused

After 3 consecutive denials the request ends and the model is told to stop and
report, rather than spending the whole turn budget re-proposing. One approval
resets the count. Tune with `denial_breaker_threshold`.

### A wrong API key is being used

Resolution order is `.env` key store → explicit `api_key` in `config.txt` →
`os.environ`. Ambient environment variables are **last**, so a stray shell
export cannot silently redirect a configured provider. See
[Resolution order](02-configuration.md#resolution-order).

## Safety

### Can a web page or an MCP server give my agent instructions?

It cannot forge a system turn. Fetched text is wrapped in
`<<<EXTERNAL_UNTRUSTED_CONTENT>>>` markers with chat-template control tokens
(`<|im_start|>`, `[/INST]`, …) stripped first. Gateway messages are sanitized
the same way, though not wrapped — the sender is the principal for that
request. See [Content defense](03-permissions-and-security.md#content-defense).

### Could it leak my API keys?

Two separate protections. Configured secrets are redacted from tool output and
from answers, longest-value-first. Separately, every outbound URL and argument
set is scanned, and a request carrying a configured credential is refused
outright — in **every** mode, with no approval prompt, because a credential in
an outbound payload is never legitimate. See
[Outbound secret guard](03-permissions-and-security.md#outbound-secret-guard).

### Is it safe to expose over MCP?

The default MCP server surface is 6 non-mutating tools. `write_file` is added
only with `mcp_server_allow_writes=1`, because MCP has no approval channel —
there is no prompt for a client to answer. `execute_shell`, `run_sandboxed`,
`browse_page`, `spawn_subagent` and the mutating git tools are never exposed in
any configuration. See [MCP server mode](07-mcp.md#server-mode).

### What happens on a scheduled run with nobody to approve?

`cron_mode=deny` (the default) refuses the gated action and tells the model to
report it. `cron_mode=approve` treats the gate as granted. Neither unlocks the
always-on floor. See [Unattended runs](03-permissions-and-security.md#unattended-runs).
