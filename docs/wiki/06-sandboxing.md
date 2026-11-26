# Sandboxing

[← Wiki index](README.md)

Shell commands and `run_sandboxed` execute inside an isolation layer so a bad
command can't reach your whole filesystem or network.

## Backends

`sandbox_backend` in `config.txt`, or `AGENT8088_SANDBOX`:

| Value | Behaviour |
|---|---|
| `auto` *(default)* | On Windows, the [WSL sandbox](#wsl-sandbox-windows) once setup has made it; then the native runtime; Docker if that is missing or fails its one-time probe; otherwise refuse execution |
| `native` | Force the free OS-level sandbox (if it is installed but fails its probe, Docker is used when available, otherwise execution is refused). On Windows this is the ACL runner |
| `docker` | Force the Docker fallback |
| `wsl` | Windows: force the WSL sandbox (if it is set up but cannot start, Docker is used when available, otherwise execution is refused) |

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
or the DSH Windows ACL runtime on Windows. On Windows with WSL 2 it also sets up
the [WSL sandbox](#wsl-sandbox-windows). No Docker daemon or container images
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

**Secret files.** On macOS and Linux a sandboxed command cannot read or change
`.env*`, `*.pem`, `*.key`, `*.p12` and `*_KEY*`, `*_SECRET*`, `*_TOKEN*`,
`*_PASSWORD*` files in the project folder and in the folder the command runs in,
nor `~/.ssh`, `~/.aws`, `~/.gnupg` and the other credential folders. On Linux the
runtime takes only literal paths for write rules, so Agent8088 lists the matching
files that exist before each command; a file the command itself creates is not
covered (the file tools refuse those names on their own). `node_modules`, `.git`,
virtual environments and build output are not scanned, and the scan stops after
50,000 entries.

On Windows the sandbox restricts file writes (a restricted token plus NTFS
ACLs on the current account); it does not filter the network, and `/sandbox`
says so.

**The Windows native backend restricts file writes only.** It does not apply
`sandbox_allowed_domains`, and a sandboxed command sees the host's process list.
`/sandbox`, `/doctor` and the start-up notice say so ("network policy not
enforced on this backend (write restriction only)"), and `/doctor` warns when
`sandbox_allowed_domains` is set there. For network isolation on Windows use the
[WSL sandbox](#wsl-sandbox-windows) or `sandbox_backend=docker`. Stopping processes by name is refused in every mode
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

To build projects that need these tools on Windows, use the
[WSL sandbox](#wsl-sandbox-windows) (`agent8088 --sandbox-setup` sets it up when
WSL 2 is installed), or run commands in Docker (needs Docker Desktop and an image
that has both Python and Node):

```ini
sandbox_backend=docker
docker_image=<an image with Python and Node>
docker_network=allowlist
sandbox_allowed_domains=registry.npmjs.org
```

Packages installed in the container are Linux builds in your project folder, so
run `npm install` again on Windows before you run the project yourself.

## Where commands work

Sandboxed shell commands start in the project folder and may write there
(`shell_workspace=project`, the default), so a build can write its output next
to its sources (`out/`, `dist/`, a `.venv`). The repository's `.git` stays
read-only to them on every backend: srt denies writes to it, Docker mounts it
read-only, and on Windows (whose runner grants one folder whole) the hooks and
`.git/config` are saved before each command and put back after it. Use the git
tools for repository changes.

When the project folder is the home folder, a folder above it, the Agent8088
data folder or a drive root, commands use `artifacts/` instead.
`shell_workspace=artifacts` always starts them in `artifacts/`, and in the project
folder only after a file you named there was written.

## Services

A shell command ends with the command that started it, so a server started by one
command is gone before the next one can test it. `service_start` runs a command in
the same sandbox a shell command would, keeps it running, and waits until it
answers on its port; `service_status`, `service_logs` and `service_stop` manage it.

- **Port.** `{port}` in the command is replaced with a free port, also in `PORT`.
  The service is reached from this computer at `http://127.0.0.1:<port>/`. On
  Docker and Linux nothing else can reach it. On Windows native and macOS the
  service binds its own address: the model is told to bind `127.0.0.1`, but a
  service that binds `0.0.0.0` there is reachable from the local network.
- **How the port gets out of the sandbox:**
  - Windows native and macOS: directly (the Windows runner does not isolate the
    network; on macOS the sandbox may listen on local ports).
  - Docker: the service stays on its own network (the allowlist network, or an
    internal one with no way out under `docker_network=none`), and a small relay
    container publishes that one port on `127.0.0.1`.
  - Linux (srt): every command has its own network namespace, so the port is
    bridged out through a unix socket. Unix sockets are allowed for that service
    only, with the container engines', the system and session buses' and the
    user runtime folder's sockets hidden from it.
- **Limits.** At most four at once. Each belongs to the agent that started it.
  All stop when the session ends; a session that crashed leaves a record, and the
  next one stops what it left running.
- `services=0` turns the tools off.

`run_with_service` is the one-command form: it starts the server, waits until its
port answers (45 s at most), runs one check command (120 s by default, 280 s at
most), then stops the server and everything it started. The check runs in the
same sandboxed command as the server, so it reaches the server on every backend,
including where a service cannot be reached from this computer. A check that
hangs is ended with everything it started. Its exit status and output count as
evidence the way a shell command's do: a test runner's result, or a failing exit.

## Testing a service in a browser

`browser_test` runs the model's own steps in headless Chromium on this computer
against a running service (the address `service_start` gave): open a path,
click, type, choose, wait, and check what the page shows (`expect_text`,
`expect_visible`, `expect_count`, ...). It stops at the first failing step and
says what the page showed instead, reports page and console errors, and saves a
screenshot of the last state.

- It reaches only a service this session started and still runs, at
  `http://127.0.0.1:<port>/`. Any other address is refused before the browser
  starts, and inside the test every request to another origin is refused too.
- A test whose `expect_*` steps all pass counts as a passing check, like a test
  run; a failing step counts as a failing check. A test with no `expect_*` step
  settles nothing.
- It is a `browser` tool: in `readonly` and plan mode it asks first.

For what a selector cannot reach, such as a canvas game or a drawing board, it
also has the mouse and the keyboard, and it can look:

- `click_at [x, y]` (relative to an element with `selector`), `hover`, `drag`
  with `to`, `type`, `press` with `times`, `hold` a key for `ms`, `keydown` and
  `keyup`, and `viewport [375, 740]` for a phone-sized screen.
- `inspect` lists the visible buttons, fields, links and canvases with their
  positions, sizes and a selector for each, to plan a test.
- `screenshot` captures the window or one element (`grid` draws labelled
  coordinates). Screenshots and `inspect` captures are attached to the result
  as images (as many as the conversation keeps, the latest ones), and so is the
  state a failing test stopped in. They are saved under
  `artifacts/screenshots/browser-test/`.
- `expect_js` checks a JavaScript expression in the page (a game's score, a
  board's turn); `js` reads a value. `expect_changed` and `expect_unchanged`
  compare an element's pixels with the last screenshot (or the page as first
  loaded).

On Docker, a service is reached through a relay container and a small forwarder
inside its own container, so a service that binds `127.0.0.1` works too.

## WSL sandbox (Windows)

On Windows with WSL 2, `agent8088 --sandbox-setup` also imports a small WSL
distro of Agent8088's own, `agent8088-sandbox`, and from then on `auto` runs
commands there under the same sandbox runtime as on Linux. That removes the
Windows runner's limits: the network allowlist is enforced, a command cannot see
the host's processes, and Node build tools (Vite, esbuild, Vitest, npm install
scripts) can start their child processes. Dev servers run as
[services](#services) and are reached on `127.0.0.1` as before.

- **What setup does.** It downloads the Alpine Linux root filesystem (about 4 MB,
  checked against a pinned SHA-256), imports it with `wsl --import` (no admin
  rights), and installs bash, Node, npm, Python 3, pip, git, bubblewrap, socat,
  ripgrep and the sandbox runtime: about 60 MB downloaded and 190 MB on disk,
  in `<data folder>/wsl/agent8088-sandbox`. It takes under a minute on a fast
  connection. Running setup again brings the distro up to date. Your own WSL
  distros are never used or changed.
- **The distro is for the sandbox only.** WSL interop is off in it, so a Linux
  command cannot start a Windows program (`cmd.exe`, `powershell.exe`) the way it
  can in an ordinary distro, and Windows' `PATH` is not added. Commands run as an
  unprivileged user, `agent`. No Windows environment variable reaches a command
  (WSL passes only the ones listed in `WSLENV`, which Agent8088 sets to its own),
  so a token set in your shell never gets in.
- **Paths.** Your drives are under `/mnt` (`E:\project` is `/mnt/e/project`).
  Windows paths in a command are rewritten to those, and a `/mnt/...` path the
  shell prints is accepted by the file tools. Folders on a network share
  (`\\server\share`) cannot be reached from the sandbox.
- **Files.** Commands may write the shell's folder (the project, or `artifacts/`),
  the sandbox temp folder and the package caches; `.git` stays read-only. Your
  home folder's credential files and the project's secret files are hidden, as on
  Linux. WSL's interop and display sockets (`/run/WSL`, `/mnt/wslg`) are hidden too.
- **Python and Node** are the distro's Linux builds: `python` and `python3` both
  work, and `pip install` installs for the sandbox's own user. A virtual environment
  made in the project is a Linux one.
- **npm projects work on both systems.** After an `npm install` (or `ci`, `add`,
  `update`) in the sandbox, Agent8088 installs the project once more for Windows, in a
  temporary folder, in the same sandbox and with no install scripts, and from it adds
  the Windows builds of packages that ship native code per platform (esbuild, and
  rollup for Vite, swc, lightningcss, sharp), puts back the files an install script
  replaced with a Linux binary, and writes Windows `.cmd` and `.ps1` shims in
  `node_modules/.bin`. So `npm run build` works in the sandbox and on Windows from
  the same `node_modules`. A package that compiles native code while it installs
  (node-gyp, a `binding.gyp`) has no Windows build to add: the result names it, and
  `npm install` on Windows is needed before the project runs there. yarn and pnpm
  installs are not adjusted.
- **Speed.** A command takes about a second longer to start than with the Windows
  runner, and the first one after Windows starts takes a few seconds while WSL
  starts. Work under `/mnt` goes through WSL's file share, so a large
  `npm install` is slower than on Linux.
- **Windows programs run in the Windows sandbox.** A command that starts one (a
  `.exe`, `.bat`, `.cmd` or `.ps1`, or `cmd`, `powershell`, `pwsh`, `dotnet`,
  `msbuild`, `nuget` and other Windows-only tools) cannot run in the distro, so it
  goes to the Windows ACL runner instead: its shell is cmd, writes are confined to
  the project, and the network is not filtered, which the result says. `/mnt/<drive>`
  paths in such a command are turned back into Windows paths. Without the Windows
  runner installed the command is refused with the reason. Everything else stays in WSL.
- **Off and removal.** `sandbox_wsl=0` (or `AGENT8088_SANDBOX_WSL=0` for the
  installer) skips it; `/sandbox native` or `/sandbox docker` stops using it
  without removing it. `agent8088 --uninstall` unregisters the distro.
- **When it cannot start** (WSL turned off, the distro removed), the next backend
  in line takes over for the session, and `/sandbox` says why.

`wsl_distro` names another distro set up the same way; the tests use one called
`agent8088-sandbox-test` so the real one is never touched.

## Docker fallback

Used automatically under `auto` when native isolation is missing or cannot run.

- **Image.** Commands run in Agent8088's sandbox image
  (`ghcr.io/palindrome-rl/agent8088-sandbox`, built from
  `docker/sandbox.Dockerfile`: Python, Node and git, on a base pinned by digest),
  so `pip install`, `npm install` and `git clone` work in one container. When it
  cannot be pulled, `python:3.11-slim` is used. `docker_image=` picks any other.
- **Network.** Docker standing in for a missing native sandbox gets
  `docker_network=allowlist`: the container joins an internal Docker network
  whose only way out is a small proxy container (`agent8088-egress-proxy-*`)
  that allows only `sandbox_allowed_domains` (the registry preset unless set).
  The proxy also refuses any name that resolves to this computer, the local
  network or a cloud metadata address, and connects to the address it checked.
  If the proxy cannot start, the container gets no network. With
  `sandbox_backend=docker` chosen on purpose, `docker_network=none` stays the
  default: no network at all.
- **Secrets.** Before each command, secret files in the folder are covered with
  an empty file, including in `build/` and `dist/`, and a `.git/config` that
  holds a token. Installed package trees (`node_modules`, `.venv`) are not
  searched.
- **Ownership.** On Linux the container runs as your user id, so the files it
  writes are yours.
- **Clean-up.** At the first command of a session, containers left by an
  Agent8088 process that has ended, and proxies for an allowlist that is no
  longer configured, are removed.
- **Docker Desktop** is not started for the sandbox unless `docker_autostart=1` (the managed SearXNG still starts it unless `docker_autostart=0`).
- **Many secret files.** A folder with four or more secret-looking files (a `certs/` or test-fixture folder) is covered with an empty folder as a whole, so its other files are hidden too. More than 200 masks in one project stops the command rather than running it unmasked.
- **Pinned images.** The fallback and proxy images are pinned by digest.

Docker's bind mounts are resolved by the Docker daemon. If Agent8088 itself is
running in a container, its workspace path is normally not visible to that
daemon, so the fallback is refused with a diagnosis rather than returning a raw
Docker error. Run Agent8088 on the Docker host or use native isolation there.

## Secrets in the environment

A sandboxed command does not inherit variables whose names look like secrets
(`TOKEN`, `SECRET`, `PASSWORD`, `PASSPHRASE`, `CREDENTIALS`, `API_KEY`,
`ACCESS_KEY` and other `*_KEY` names, `DATABASE_URL`), so code the model runs
cannot read a token set in your shell. srt unsets them on macOS and Linux, the
Windows runner drops them, and Docker containers never inherit the host
environment. Name any variable a task really needs inside the sandbox with
`sandbox_pass_env=GITHUB_TOKEN,NPM_TOKEN`.

## Network egress

Sandboxed commands reach only the hosts in `sandbox_allowed_domains`
(**macOS, Linux and Docker** -- the Windows native backend does not enforce this,
see above). With no `sandbox_allowed_domains` line the list is `default`: the
common package registries, code hosts and docs sites (PyPI, npm, GitHub,
crates.io, Go proxy, Maven Central, RubyGems, conda, Hugging Face, ...), so
`npm install` / `pip install` / `git clone` work out of the box. Add your own
hosts after it, or close the network with `none`:

```ini
sandbox_allowed_domains=default,api.example.com
sandbox_allowed_domains=none
```

When a command is refused a host, the model is told which host it was and that
retrying will not help, and the refusal is written to the audit log. In an
interactive session you are also asked once whether to allow that host for the
rest of the session; a host you decline is not asked about again, and
`sandbox_ask_hosts=0` turns the question off. Nothing is written to `config.txt`.

Package caches (pip, uv, npm, yarn, pnpm, cargo, Go) go to `sandbox-cache` in the
Agent8088 data folder, which sandboxed commands may write and which is kept
between commands. On macOS, commands may also listen on local ports, so a dev
server or test runner can start inside the sandbox.

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

Run `/sandbox` in the REPL to check the selected backend and verification state.

The source checkout has attack tests (`tests/test_sandbox_attacks.py`): real
commands that try to write outside the folder, read a key in the home folder or a
`.env` in the project, reach hosts off the allowlist, private and metadata
addresses, open a raw connection past the proxy, read a secret variable, and plant
a git hook. They run on every backend the machine has and check the result on the
host. They are opt-in:

```bash
AGENT8088_ATTACK_TESTS=1 python -m pytest -q -rs tests/test_sandbox_attacks.py
AGENT8088_ATTACK_BACKENDS=docker AGENT8088_ATTACK_TESTS=1 python -m pytest -q -rs tests/test_sandbox_attacks.py
```

On Windows, `AGENT8088_ATTACK_BACKENDS=wsl` runs them in the WSL sandbox too, with
four more that only it promises: a Windows program cannot be started from it, Node
can start a child with piped output, a timed-out command stops inside the distro,
and a stopped service leaves nothing running. They use the distro
`AGENT8088_WSL_DISTRO` names (default `agent8088-sandbox`), set up beforehand.

A test the backend cannot enforce is skipped with the reason (the Windows runner
limits writes only), so a skip is the documented gap, not a pass. CI runs them on
Linux (srt and Docker) and macOS (srt). The source checkout also includes `scripts/verify_features.py`; checks that execute shell commands require a working sandbox. See [Testing & Verification](12-testing-and-verification.md).
