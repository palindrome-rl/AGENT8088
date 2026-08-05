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

The MCP server runs the engine in full-auto *because* it cannot prompt; that is
only safe while the exposed set is non-mutating, which a test enforces. See
[MCP](07-mcp.md#server-mode).

## Verifying any of this yourself

Every claim above is covered by the suites:

```sh
AGENT8088_CONFIG=/nonexistent python -m pytest tests/test_permission.py tests/test_security_fixes.py tests/test_ssrf.py -v
```

See [Testing & Verification](12-testing-and-verification.md).
