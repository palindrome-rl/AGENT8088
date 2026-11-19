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

Installs the native sandbox runtime: Anthropic's sandbox runtime on macOS/Linux
or the DSH Windows ACL runtime on Windows. No Docker daemon or container images
are needed.

**Prerequisites:**

| Platform | Needs |
|---|---|
| macOS | Node.js 20.11+, `ripgrep` |
| Linux | Node.js 20.11+, `bubblewrap`, `socat`, `ripgrep` |
| Windows | Supported Node.js (managed automatically if needed); setup runs as the current user, with no admin elevation or separate sandbox account |

On Windows, setup and runtime both prefer Agent8088's managed Node when it
meets the Node.js 20.11+ requirement. An older managed installation is skipped
in favor of a supported PATH version. If neither is suitable, sandbox setup
downloads the pinned Node.js 22.11.0 build and verifies its SHA-256 checksum.
Setup requires npm; running an already-installed sandbox only requires Node.

On Windows the sandbox restricts file writes (a restricted token plus NTFS
ACLs on the current account); it does not filter the network, and `/sandbox`
says so.

**The Windows native backend restricts file writes only.** It does not apply
`sandbox_allowed_domains`, and a sandboxed command sees the host's process list.
`/sandbox`, `/doctor` and the start-up notice say so ("network policy not
enforced on this backend (write restriction only)"), and `/doctor` warns when
`sandbox_allowed_domains` is set there. For network isolation on Windows use
`sandbox_backend=docker`. Stopping processes by name is refused in every mode
for the same reason (see [permissions](03-permissions-and-security.md)).

**The Windows native backend also blocks child processes with piped output.**
A program that starts another program and captures its output (Node's default)
fails with `spawn EPERM` inside it. That stops esbuild and the tools built on it
(Vite, Vitest, tsx), `npm install` scripts for packages such as esbuild, and
other Node build tooling. Python's `subprocess`, `node file.js` and programs
whose children write straight to the console still work. Every process also
ends when the command that started it ends, so a dev server cannot be left
running for a later command. Plain HTML, CSS and JavaScript apps, Python
scripts and `check_app` are not affected. The startup check passes either way;
Agent8088 runs a second probe on Windows and, when it fails, `/doctor` and the
start-up notice show "sandbox child processes: limited", and a `spawn EPERM`
result tells the model the real cause. macOS and Linux use different
sandboxes and are not affected.

To build projects that need these tools on Windows, run commands in Docker
(needs Docker Desktop and an image that has both Python and Node):

```ini
sandbox_backend=docker
docker_image=<an image with Python and Node>
docker_network=allowlist
sandbox_allowed_domains=registry.npmjs.org
```

Packages installed in the container are Linux builds in your project folder, so
run `npm install` again on Windows before you run the project yourself.

## Docker fallback

Used automatically under `auto` when native isolation is missing or cannot run:

```ini
docker_image=python:3.11-slim
docker_network=none
```

`docker_network=none` is the safer default — no network from inside the
container at all. `docker_network=allowlist` gives the container the same
allowlist the native runtime enforces: it joins an internal Docker network
whose only way out is a small proxy container (`agent8088-egress-proxy-*`,
left running between sessions) that allows only `sandbox_allowed_domains`.
If the proxy cannot start, the container gets no network.

Docker's bind mounts are resolved by the Docker daemon. If Agent8088 itself is
running in a container, its workspace path is normally not visible to that
daemon, so the fallback is refused with a diagnosis rather than returning a raw
Docker error. Run Agent8088 on the Docker host or use native isolation there.

## Network egress

Sandboxed commands have no network unless you allow specific domains
(**macOS, Linux and Docker** -- the Windows native backend does not enforce this,
see above):

```ini
sandbox_allowed_domains=api.example.com,pypi.org
```

`default` in the list adds the common package registries, code hosts and docs
sites (PyPI, npm, GitHub, crates.io, Go proxy, Maven Central, RubyGems, conda,
Hugging Face, ...), so `sandbox_allowed_domains=default` is enough for most
`npm install` / `pip install` / `git clone` tasks.

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
