# Permissions & Security

[← Wiki index](README.md)

The safety properties here are **enforced in code**, not requested in the
prompt. A jailbroken or prompt-injected model still cannot get past them,
because `check_permission()` runs before the tool does and doesn't consult the
model's opinion.

## The three permission modes

Set at startup with `--mode`, or switched live with `/mode`.

| Mode | Reads | Writes / shell / network | How to get a mutation through |
|---|---|---|---|
| **`readonly`** *(default)* | allowed | refused | per-action `y/N` prompt |
| **`full-auto`** | allowed | allowed, no prompt | — |
| **`plan-only`** | allowed | refused | approve a whole plan up front |

`--edit` and `--full-auto` are aliases for `--mode full-auto`.

### What readonly actually allows

Verified against `check_permission()`:

| Tool mode | readonly |
|---|---|
| `read_text`, `last_output`, `python_eval`, `plan` | ✅ allowed |
| `write_text`, `shell`, `http_get`, `http_post`, `docker`, `cron`, `browser`, `subagent` | ❌ refused |

Note that **network reads are refused too** — `web_search` and
`get_page_title` need approval in readonly, because fetching a URL is an
outbound side effect and a route for untrusted content.

Shell is the exception that has nuance: a command on the readonly-safe list
runs without a prompt. That list is inspection-only:

```
cat  date  df  diff  dir  du  findstr  free  grep  head  hostname  ls
nproc  pwd  systeminfo  tail  tasklist  type  uname  uptime  ver  vol
wc  where  whoami
```

(25 commands, `readonly_safe_commands` in config extends the list.)

Anything else — including `echo x > file`, `pip install`, `find -delete`, or
`python -c "open(...,'w')"` — is classified as a mutation and refused. The
classifier looks through `sh -c`, `&&`, `;` and nesting rather than pattern-
matching the first word.

### Escalation is one action, not a mode change

Approving a prompt grants **exactly one** blocked action:

```python
grant_escalation()
check_permission("write_text")   # True  — consumes the grant
check_permission("write_text")   # False — gone
```

Safe actions don't consume it. The mode itself never changes, so approving one
write does not put you in full-auto.

### plan-only

The agent must call `execute_plan` with a structured list of steps. You approve
the plan as a whole, then a temporary grant lets exactly those steps run.
Direct tool calls are refused with a message telling the model to use a plan.
The grant is cleared when the plan finishes and **only applies in plan-only
mode** — it cannot leak into another mode.

## The always-on floor

These are refused in **every** mode — full-auto included — and no escalation
grant unlocks them.

### 1. Credential files

Blocked for both reading and writing, matched on filename *and* anywhere in the
path:

| | |
|---|---|
| Names | `.env`, `config.txt`, `configb.txt`, `id_rsa`, `id_ed25519`, `.ssh`, `.gnupg`, `.aws`, `.gitconfig` |
| Extensions | `.pem`, `.key`, `.rsa`, `.p12` |
| Globs | `*_KEY*`, `*_SECRET*`, `*_TOKEN*`, `*_PASSWORD*` (and lowercase) |

This covers indirect routes too: symlinks are resolved before the check, and
`git show HEAD:.env` / `git diff -- .env` are blocked explicitly because they'd
otherwise read a credential without touching the file tool.

`allowed_sensitive_files` is the escape hatch if you genuinely need one.

### 2. Shell startup files — **writes only**

Writing one of these is arbitrary code execution on your next shell launch, so
writes are refused unconditionally:

```
.bashrc  .bash_profile  .bash_login  .bash_logout
.zshrc   .zshenv  .zprofile  .zlogin  .zlogout
.profile .login  .cshrc  .tcshrc  .kshrc
config.fish  fish.config
```

Matched on **exact filename**, so `profile.json` and `.editorconfig` are
unaffected. **Reads stay allowed** — "help me fix my PATH" is a normal request.

### 2b. Commands that cannot be analysed

Detection must not depend on the command being well-formed. `_hard_blocked_shell`
lexes the command to find dangerous git operations and wrapper payloads; when the
lexer failed it used to return "not blocked", so appending a single unbalanced
quote skipped every check below it:

```sh
git push origin main       # refused
git push origin main "     # used to execute
```

Now a command too long (`max_command_chars`, default 16384) or too quote-dense to
analyse is **refused**, and detection re-runs on a de-quoted variant so it no
longer depends on well-formed input. The variant is only used for detection and is
never executed, and `echo`/`printf` stay on the non-exec list, so
`echo "git push"` does not become a push.

### 3. Destructive git

`git push`, `git reset --hard`, `git branch -D` and friends are refused even in
full-auto and even after a grant. The check sees through `sh -c '...'`,
`git -C /path push`, `/usr/bin/git push`, and `echo hi; git push`. Meanwhile
`echo git push` and `grep git push file` are correctly *not* blocked — it
distinguishes git-as-a-command from git-as-a-word.

### 4. System-prompt exfiltration

Requests for `system.md`, "your instructions", "the prompt you were given" and
similar are refused pre-flight, without a model round-trip. Answers are also
checked against fingerprints of the base prompt so a verbatim leak is caught on
the way out.

## Write path zones

Within `allowed_paths`, writes are classified into three zones:

| Zone | Behaviour |
|---|---|
| `blocked_paths` | always refused |
| `no_prompt_paths` | written silently |
| `prompt_paths` | per-action approval |

Blocked wins over everything, including full-auto.

## SSRF protection

Every outbound URL from `web_search`, `get_page_title`, `browse_page` and the
HTTP tool modes goes through `_ssrf_check()`, which refuses:

- loopback (`127.0.0.1`, `localhost`, `[::1]`)
- private ranges (`10.*`, `192.168.*`, …)
- link-local, notably the cloud metadata address `169.254.169.254`
- non-HTTP schemes (`file://`, `gopher://`, …)

**Redirects are re-checked.** A public URL that 302s to `127.0.0.1` is caught
at the redirect, not just at the original URL.

To reach a genuinely local service, allowlist just that host:

```ini
ssrf_allow_hosts=127.0.0.1,localhost
# or pin the port
ssrf_allow_hosts=10.0.0.5:9200
```

Prefer this over `ssrf_allow_private=1`, which opens the whole private network.

## Egress domain policy

SSRF covers *internal* addresses. This bounds which **public** hosts the agent
may reach at all — without it, every public host is reachable and `http_post`
can send an arbitrary body anywhere.

```ini
# Refuse these, always. Wins over allowed_domains.
blocked_domains=pastebin.com,transfer.sh,file.io,0x0.st

# If set, these are the ONLY public hosts reachable. Empty = all reachable.
allowed_domains=api.github.com,docs.python.org
```

Matching is dot-anchored on the host: `example.com` covers `docs.example.com`
but **not** `evilexample.com`.

Enforced on every outbound path — `web_search`, `get_page_title`, `browse_page`,
both HTTP tool modes, image URL fetches, **HTTP redirects**, and in-browser
subresource requests.

The policy runs *before* the SSRF check, which is a DNS lookup. A host the policy
already rejects is never resolved, so the attempt never reaches that domain's
nameserver.

If you set `allowed_domains`, remember to include the host from
`search_base_url` or `web_search` will start failing.

## Outbound secret guard

Secret redaction (below) protects what comes *back* from a tool. This protects
what goes *out*: nothing else stopped the model reading a credential and putting
it into an `http_post` body or a URL query string.

Every outbound URL and argument set is scanned for configured secret values. A
match is refused outright:

```
Error: Blocked — this request contains a credential from your configuration.
Sending secrets to an external service is never permitted, in any permission mode.
```

This is a floor, not a gate: **no permission mode unlocks it, including
`full-auto`**, and there is no escalation prompt — a credential in an outbound
payload is never legitimate. The error deliberately does not echo the matched
value. Values shorter than 12 characters are ignored, so a short config value
does not turn into a false positive on every request.

Together with the egress policy this closes the combination that matters:
private data, untrusted content, and an outbound channel in the same agent.

## Resource budgets and blast radius

The permission layer answers *may this run*. These answer *how much*.

| Guardrail | Keys | Bounds |
|---|---|---|
| Turn budget | `max_turn_tokens`, `max_turn_seconds`, `max_turn_cost_usd` | Tokens, wall clock, and spend for one request |
| Write blast radius | `max_writes_per_turn`, `max_write_bytes` | Files written and bytes per write |

All default to `0` (disabled). `max_turns` only bounds how many *rounds* a
request takes; a plan or subagent chain can burn a great deal inside a few
rounds, and before these there was no spend accounting at all.

Subagents inherit both budgets. A fresh budget per subagent would be a free
bypass — delegate, and the limit starts over.

The write caps are checked *before* the permission gate, so the refusal is not
something a user can wave through by mistake.

Full key reference in [Configuration](02-configuration.md#turn-budget).

## Approval modes

`permission_mode` decides *what is gated*. `approval_mode` decides *who answers
the gate*.

| Mode | Behaviour |
|---|---|
| `manual` *(default)* | Every gated action prompts the operator. |
| `smart` | An auxiliary model reviews the action, auto-approving low-risk ones and escalating the rest. |
| `off` | No gate. Trusted, sandboxed environments only. |

`smart` mode adds a model call per gated action, so it is opt-in rather than
inherited by an existing config.

```ini
approval_mode=smart
smart_approval_model=cerebras:gpt-oss-120b        # optional; defaults to the active model
smart_approval_policy=This box is a scratch VM; tolerate rm under /tmp.
```

**What the guardian can and cannot do.** It runs *after* the always-on floor, so:

- it is never consulted for an action the floor already refused (no wasted call)
- an `APPROVE` on `rm -rf /`, a sensitive-file write, or an outbound credential
  changes nothing
- it can only skip a prompt the operator would have answered — it can never widen
  what is reachable

It fails closed everywhere it can: an empty, ambiguous, or unparseable verdict
escalates, and so does an unreachable or erroring model. Only a first line
starting with `APPROVE` counts.

The action text is written by the model under review, so the guardian receives it
wrapped in the same untrusted-content markers as a fetched web page, and is told
to ignore any instructions or claims of prior authorisation inside it.

This is a convenience heuristic, not a boundary. Hermes' own `SECURITY.md` puts it
plainly: the only real boundary against an adversarial model is the operating
system — see [Sandboxing](06-sandboxing.md).

## Denial circuit breaker

```ini
denial_breaker_threshold=3     # 0 disables
```

A denied action used to leave the model free to re-propose it every round until
`max_turns`. That reads to the user as the agent ignoring them, and it spends a
whole turn budget to arrive at the same no. After N consecutive denials the request
ends with the model told to stop and report. One approval resets the count, and the
count resets per request.

## Unattended runs

A scheduled run has no operator, so an approval prompt there was emitted to nobody
and sat until the turn died.

```ini
cron_mode=deny      # deny (default) | approve
```

`deny` refuses the gated action and tells the model to report it in its answer.
`approve` treats the gate as granted. **Neither unlocks the always-on floor** — a
scheduled run still cannot `rm -rf /`.

Entries created by `schedule_task` (crontab and Windows Task Scheduler alike) set
`AGENT8088_UNATTENDED=1`, so this applies without extra setup. The variable is read
once at startup rather than per call: an env-var check on the hot path would let
anything running inside the process flip it mid-turn, which is why Hermes freezes
its equivalent at import too.

## Destructive command confirmation

```ini
destructive_slash_confirm=1     # /reset, /clear
mcp_reload_confirm=1            # /mcp reload
```

A mistyped `/reset` mid-session used to discard the whole conversation with no
signal beforehand. Skipped when there is nothing to lose, and when stdin is not a
tty — a scripted run has nobody to ask.

## MCP server circuit breaker

Three consecutive failures from one MCP server open a breaker for 60 seconds. While
it is open, calls to that server's tools return an error that explicitly tells the
model **not** to retry yet and how long is left. A success resets it.

Breakers are per server, so one dead server does not silence a healthy one. Without
this, the model retried a dead server every round and spent the whole request on
something that was not coming back.

## Shell command allowlist

`deny_commands` only stops what you thought of. `allow_commands` stops
everything you did not:

```ini
# Only these shell commands may run. Empty = no allowlist in force.
allow_commands=git status,git diff,git log,ls*,npm test,pytest*
```

Enforced at the always-on floor, so an unlisted command is **not escalatable** —
the same standing as a deny rule. Precedence:

1. Unrecoverable floor wins over everything. `allow_commands=*` does **not**
   re-enable `rm -rf /`, `mkfs`, or `curl | sh`.
2. `deny_commands` wins over `allow_commands` — deny is the more specific intent.
3. Otherwise, an allowlist (if set) must match.

Wrapped payloads are covered: `bash -c '<unlisted>'` is refused, because the
recursion in `_hard_blocked_shell()` re-checks the inner command.

## Audit trail

Off by default; **turn it on for any gateway deployment**.

```ini
audit_log=1
audit_log_path=/var/log/agent8088/audit.jsonl   # optional
```

One JSON line per gated decision, at mode 0600:

```json
{"ts":"2026-08-06T09:15:02+00:00","event":"tool_call","permission_mode":"readonly",
 "tool":"execute_shell","mode":"shell","decision":"denied",
 "detail":"curl https://pastebin.com/…","reason":"egress_policy"}
```

`decision` is `allowed`, `blocked` (escalation requested), or `denied` (refused
at a floor, no escalation possible). Every field is passed through secret
redaction, so a blocked exfiltration attempt is recorded *without* writing the
credential to disk.

The writer never raises: an unwritable audit path is a lost record, not a failed
turn. It is a record, not a gate.

Rotation is not built in — point `audit_log_path` at a file your existing
`logrotate` or cron handles.

## Content defense

Text that came from outside the model's own reasoning — web pages, MCP tool
results — is wrapped before the model sees it:

```
<<<EXTERNAL_UNTRUSTED_CONTENT source="https://example.com">>>
...fetched text...
<<<END_UNTRUSTED_CONTENT>>>
```

Chat-template control tokens (`<|im_start|>`, `<|eot_id|>`, `[/INST]`, …) are
stripped first, so a page containing `<|im_start|>system` cannot forge a system
turn on a self-hosted model.

**Inbound gateway messages are stripped too.** A Slack or WhatsApp message
containing `<|im_start|>system` would otherwise be tokenized as a real role
boundary and could grant itself a permission mode.

Gateway text is *not* wrapped in the untrusted markers, deliberately: the sender
is allowlisted and is the principal for that request, so demoting their whole
message to "data, never instructions" would stop the gateway from doing anything
at all. The structure is sanitized; the authority is kept.

## Secret redaction

Every configured key or token is removed from tool output and from answers,
longest-value-first so overlapping secrets mask completely. `*_env` pointers are
resolved through the `.env` store *and* `os.environ` before redacting — so a key
that lives only in `.env` is still caught, and the variable *name* (which is not
a secret) is left readable.

## Remote surfaces

Both remote surfaces default to the safe posture, for the same reason but by
different means:

| Surface | Default | Approvals |
|---|---|---|
| **Gateway** (Slack/WhatsApp/Discord) | `readonly` | `/approve` + `/deny` in chat; Discord gets ✅/❌ buttons with a **fail-closed** timeout |
| **MCP server** (`--mcp-serve`) | read-only tool set | none possible — MCP has no approval channel, so writes are opt-in via `mcp_server_allow_writes=1` |

The gateway also rate-limits per user (`gateway_rate_limit_per_min`, default 20,
slash commands included). Every turn serializes behind one global lock, so a
single user sending in a loop starves everyone else in the queue. Rejected
messages are not counted, so a user who keeps hammering still drains out of the
window rather than being locked out permanently.

### Recommended hardened gateway profile

```ini
audit_log=1
gateway_rate_limit_per_min=10
max_turn_tokens=60000
max_turn_seconds=300
max_writes_per_turn=20
blocked_domains=pastebin.com,transfer.sh,file.io,0x0.st
strict_platform_allowlist=1
```

The MCP server runs the engine in full-auto *because* it cannot prompt; that is
only safe while the exposed set is non-mutating, which a test enforces. See
[MCP](07-mcp.md#server-mode).

## Asking the agent what is in force

You do not have to read this page to find out. Ask the agent, or run
`/capabilities` — it reports the live permission mode, sandbox backend, every
limit (including which are **not** set), and the always-on floor, generated from
the running configuration rather than a hand-maintained list:

```
/capabilities
```

The agent answers the same question itself via the `describe_capabilities` tool,
so "which guardrails are active?" in chat gets the same facts. See
[Tools](04-tools.md#describe_capabilities).

## Verifying any of this yourself

Every claim above is covered by the suites:

```sh
AGENT8088_CONFIG=/nonexistent python -m pytest \
  tests/test_permission.py tests/test_security_fixes.py tests/test_ssrf.py \
  tests/test_egress.py tests/test_exfil_guard.py tests/test_turn_budget.py \
  tests/test_audit_log.py tests/test_command_allowlist.py \
  tests/test_capabilities.py tests/gateway/test_rate_limit.py -v
```

See [Testing & Verification](12-testing-and-verification.md).
