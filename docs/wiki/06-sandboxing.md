# Sandboxing

[← Wiki index](README.md)

Shell commands and `run_sandboxed` execute inside an isolation layer so a bad
command can't reach your whole filesystem or network.

## Backends

`sandbox_backend` in `config.txt`, or `AGENT8088_SANDBOX`:

| Value | Behaviour |
|---|---|
| `auto` *(default)* | Native runtime first, Docker if it is missing or fails its one-time probe, otherwise refuse execution |
| `native` | Force the free OS-level sandbox (if it is installed but fails its probe, Docker is used when available, otherwise execution is refused) |
| `docker` | Force the Docker fallback |

Check what's active:

```
/sandbox
```

The status includes whether native isolation is `verified`, still `unverified`,
or has `failed`. The first sandbox use runs one harmless command and caches that
result for the rest of the process, so merely having `bwrap` or `sandbox-exec`
on `PATH` is not treated as proof that it works.

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

Used automatically under `auto` when native isolation is missing or cannot run:

```ini
docker_image=python:3.11-slim
docker_network=none
```

`docker_network=none` is the safer default — no network from inside the
container at all.

Docker's bind mounts are resolved by the Docker daemon. If Agent8088 itself is
running in a container, its workspace path is normally not visible to that
daemon, so the fallback is refused with a diagnosis rather than returning a raw
Docker error. Run Agent8088 on the Docker host or use native isolation there.

## Network egress

Sandboxed commands have no network unless you allow specific domains:

```ini
sandbox_allowed_domains=api.example.com,pypi.org
```

This is separate from the SSRF allowlist: `sandbox_allowed_domains` governs what
a *sandboxed command* may reach; `ssrf_allow_hosts` governs what the *HTTP
tools* may reach. Shell commands that invoke a web client such as `curl` or
`wget` must contain an explicit HTTP(S) URL; that URL is checked by the same
domain and SSRF policies before the command can run. Both layers apply
independently.

A command that only *names* a client is not a fetch and needs no URL: lookups
(`command -v wget`, `which curl`, `curl --version`), text search and printing
(`grep -rn curl src/`, `echo 'install wget'`), package installs
(`apt-get install curl`) and git text (`git commit -m 'drop curl'`). The
exemption is void if anything in the command could run text as a command —
`sh`, `xargs`, `env`, `sudo` with options, `$(...)`, backticks, a newline — so
`which curl && curl host`, `env curl host` and `echo curl host | sh` are still
refused.

A blocked fetch only says the host could not be reached, so a model can spend a
whole turn on workarounds. After the **third** network failure from sandboxed
code in one turn, Agent8088 appends one note to the result. It names the setting
and the hosts the task asked for, then tells the model to stop and let the user
decide. On the Docker fallback, which has no network at all, it names
`agent8088 --sandbox-setup` first. The note never includes a URL path, a query
or any other config value, and the model cannot change `config.txt` itself. It
is not shown earlier because a small model told about the restriction up front
tends to give up before trying. This is one case of the general config-blocker
notes described in [Permissions and security](03-permissions-and-security.md).

## No unsandboxed fallback

When neither backend is available, Agent8088 refuses shell and code execution
and explains how to install the native runtime or Docker. Approval cannot bypass
this requirement.

Commands start in `artifacts/`, the only project directory they may write. A
read-only auditor runs tests in a disposable copy, so runtime files created by a
test disappear afterward and the real workspace remains unchanged.

When a sandboxed command fails with an access error (`Access is denied`,
`Permission denied`, `os error 5`), the result gets a `[sandbox]` note naming
the writable folder and saying that creating virtual environments or installing
packages from the shell will not work. The boundary itself is unchanged; the note
only stops the model from probing folder after folder to find it.

`run_sandboxed` passes the snippet to Python base64-encoded, because `cmd.exe`
ends a command at its first newline and would otherwise run only the first line
of multi-line code.

## What sandboxing does *not* cover

Worth being precise, because it's easy to over-trust:

- **The permission layer is separate.** Sandboxing limits what a command can
  reach; `check_permission()` decides whether it runs at all. A dangerous
  command is refused before the sandbox is even consulted.
- **File tools don't go through it.** `read_text` / `write_file` are gated by
  path zones and the sensitive-file floor, not by the sandbox.
- **Host-side workflow tools remain explicit.** Structured operations such as a
  user-approved commit or push are permission-gated separately; arbitrary code
  never uses that path.

## Interaction with git tools

`git status` / `git diff` / `git log` typed through `execute_shell` are
sandboxed like any other command, and are refused when no sandbox is available.
The dedicated `git_*` tools are host tools (`host=1` in `tools.txt`): they run
on the host regardless of backend and are gated by the permission layer
instead (in `readonly`, `git_status`/`git_diff`/`git_log` ask first).
`git show HEAD:.env` is separately blocked outright in every backend.

## Verifying it works

Run `/sandbox` in the REPL to check the selected backend and verification state. The source checkout also includes `scripts/verify_features.py`; checks that execute shell commands require a working sandbox. See [Testing & Verification](12-testing-and-verification.md).
