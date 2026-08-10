# Sandboxing

[← Wiki index](README.md)

Shell commands and `run_sandboxed` execute inside an isolation layer so a bad
command can't reach your whole filesystem or network.

## Backends

`sandbox_backend` in `config.txt`, or `AGENT8088_SANDBOX`:

| Value | Behaviour |
|---|---|
| `auto` *(default)* | Native runtime first, Docker if unavailable, then ask before running locally |
| `native` | Force the free OS-level sandbox |
| `docker` | Force the Docker fallback |
| `local` | **No isolation.** Explicit opt-in only |

Check what's active:

```
/sandbox
```

## Native sandbox (recommended)

```sh
agent8088 --sandbox-setup
```

Installs the open-source Anthropic sandbox runtime — no Docker daemon, no
container images, low overhead.

**Prerequisites:**

| Platform | Needs |
|---|---|
| macOS | Node.js 20.11+, `ripgrep` |
| Linux | Node.js 20.11+, `bubblewrap`, `socat`, `ripgrep` |
| Windows | Node.js 20.11+, one UAC prompt to create a restricted sandbox account |

The Windows prompt provisions a low-privilege local account that sandboxed
commands run as — that's why it's a one-time elevation.

## Docker fallback

Used automatically under `auto` when the native runtime is missing:

```ini
docker_image=python:3.11-slim
docker_network=none
```

`docker_network=none` is the safer default — no network from inside the
container at all.

## Network egress

Sandboxed commands have no network unless you allow specific domains:

```ini
sandbox_allowed_domains=api.example.com,pypi.org
```

This is separate from the SSRF allowlist: `sandbox_allowed_domains` governs what
a *sandboxed command* may reach; `ssrf_allow_hosts` governs what the *HTTP
tools* may reach. Both apply independently.

## Local execution and consent

Under `local` — or `auto` with neither backend present — Agent8088 asks before
each command:

```
Run this command locally without isolation? ls -la
```

The command shown is passed through secret redaction first, so a command
containing a key doesn't print it back at you.

Permission mode never bypasses missing isolation. Under `local` — or when
`auto` has neither native nor Docker available — each local command needs an
explicit one-shot escalation. The always-on floor still applies: catastrophic
commands and destructive git are refused regardless of backend or mode.

## What sandboxing does *not* cover

Worth being precise, because it's easy to over-trust:

- **The permission layer is separate.** Sandboxing limits what a command can
  reach; `check_permission()` decides whether it runs at all. A dangerous
  command is refused before the sandbox is even consulted.
- **File tools don't go through it.** `read_text` / `write_file` are gated by
  path zones and the sensitive-file floor, not by the sandbox.
- **`local` means local.** Choosing it disables isolation entirely. It exists
  for trusted commands and debugging, not as a normal setting.

## Interaction with git tools

Under the native sandbox, `git status` / `git diff` / `git log` run without a
prompt — the sandbox contains them. Under `local` they escalate instead, because
reading a repo unsandboxed can surface credential content (e.g. via
`git show HEAD:.env`, which is separately blocked outright).

## Verifying it works

```sh
VERIFY_HOME="$(mktemp -d)"
AGENT8088_CONFIG=/nonexistent AGENT8088_HOME="$VERIFY_HOME" \
  python scripts/verify_features.py
rm -rf -- "$VERIFY_HOME"
```

Section 3 covers sandboxing and reports the resolved backend. If the native
runtime isn't installed you get an explicit `⊘ SKIP` naming the missing
dependency rather than a silent pass — see
[Testing & Verification](12-testing-and-verification.md).
