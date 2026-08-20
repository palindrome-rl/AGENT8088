# Windows ACL Native Sandbox (DeepSeek Harness backend) Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Give Agent8088 a working native sandbox on Windows by adopting `@deepseek-ai/dsh-sandbox-windows-acl` (MIT, restricted-token + NTFS ACL write confinement) as the Windows-only native backend, replacing the currently-disabled `srt-win.exe` path. Linux (bwrap) and macOS (sandbox-exec) stay on `@anthropic-ai/sandbox-runtime` unchanged; Docker stays the fallback everywhere.

**Architecture:** `_resolve_sandbox_backend()` (`engine.py:3370`) already tries native-first-then-docker and needs no change. Only the Windows implementation of "native" changes: `_native_sandbox_argv()` gains a win32 branch that shells out to the DSH runner (`node runner.js --workspace … --temp … --mode … -- <argv>`) instead of `srt`'s `--settings <file> -c <command-string>` CLI. Because the DSH runner has **no shell built in** — it execs `<argv>` directly, no `&&`/env-var support — and Agent8088's existing command construction is POSIX shell text (`cd X && TMPDIR=Y cmd`), a small Windows-only command translator is required. This is the one piece of real design work in this plan; everything else is mechanical wiring of an already-proven argv-prefix contract.

**Tech Stack:** Python 3.12 (`engine.py`, `cli.py`), PowerShell (`install.ps1`), Node.js 20.11+ (the DSH runner), `pytest`.

---

## Context verified against `development` @ `9ee90f9` (current HEAD)

Corrections vs. the original findings doc (`native-windows-sandbox-findings.md`) — read that doc for full background/rationale, but use *this* section for exact facts:

- **Sandbox provisioning does NOT live in `install.ps1`.** It's `install_native_sandbox()` in `engine.py:4111-4151`, triggered by the `--sandbox-setup` CLI flag (`cli.py:4930`, dispatched at `cli.py:4960-4962`: `print(A.install_native_sandbox()); return 0 if A.native_sandbox_verified() else 1`). `install.ps1`'s `Install-Native-Sandbox` (`install.ps1:976-978`) is currently just:
  ```powershell
  function Install-Native-Sandbox {
      Write-Info "Native sandbox not set up - Docker will be used for sandboxing if available."
  }
  ```
  It never calls `install_native_sandbox()` on Windows today — the preceding comment block (`install.ps1:953-975`) documents why (the `CreateProcessWithLogonW`/`ERROR_ACCESS_DENIED` bug). "Re-enable Stage 5d" means making this function actually invoke `agent8088 --sandbox-setup` again.
- **`install_native_sandbox()`'s current Windows branch** (`engine.py:4134-4140`) runs `node <sandbox-runtime-cli.js> windows-install` — this is the step that provisions the `srt-sandbox` account. This whole branch is replaced, not extended.
- **Exact current line numbers**, confirmed by direct read (not just grep):
  - `_native_sandbox_argv()` — `engine.py:3319-3333`
  - `_native_sandbox_missing_requirements()` — `engine.py:3336-3345` (Windows falls to `else: required = ()` — no extra checks today, not a hard-coded "always unavailable")
  - `_resolve_sandbox_backend()` — `engine.py:3370-3383` (no change needed)
  - `_native_sandbox_repair_hint()` — `engine.py:3523-3558`
  - `_exec_native_sandbox()` — `engine.py:3805-3817`
  - `_native_sandbox_ready()` — `engine.py:3590-3638` (the one-time real probe before trusting "native" as resolved)
  - `_exec_sandbox_argv()` — `engine.py:3820-3850` (a second call site building the same `--settings/-c` invocation, for structured-argv git tools)
  - `install_native_sandbox()` — `engine.py:4111-4151`
- **`SANDBOX_RUNTIME_VERSION`** (`engine.py:520`, default `"0.0.73"` at `engine.py:504`) and `sandbox_runtime_version=0.0.73` (`src/agent8088/config.txt:84`) pin the *existing* Anthropic runtime for Linux/macOS — untouched by this plan. `sandbox_allowed_domains` is commented out/empty at `config.txt:85` by default, which is why losing Windows egress isolation is an acceptable trade-off today (see Risks).
- **No sandbox tests exist in the current tree at all.** `tests/` has no `test_sandbox*.py` on `development` HEAD (a `test_sandbox_fallback.py` exists only in stale `.claude/worktrees/*` copies, not this branch). This plan adds sandbox test coverage from scratch — it is not "extending existing coverage."
- **`scripts/verify_native_sandbox.py`** is a separate, real end-to-end probe (spawns a subprocess, drives `engine._exec_sandbox_command(...)` — the public cross-platform entry point — through workspace-write / credential-read-denial / network-egress-denial / timeout / fallback checks). Its `_require_prerequisites()` (`scripts/verify_native_sandbox.py:43-51`) has `{"darwin": (...), "linux": (...)}.get(sys.platform, ())` → win32 gets zero prerequisite/contract checks today.
- **The DSH package is pre-1.0**: `@deepseek-ai/dsh-sandbox-windows-acl@0.1.0-rc.7` (release candidate, not a stable release). Its only *runtime* dependency is `koffi@^3.1.0` (a Node FFI addon with prebuilt Windows x64/arm64 binaries) — confirmed by reading `lib/runner.js` and `lib/types-CNjZgO4h.js`, whose only imports are `node:fs`, `node:path`, `node:crypto`, and `koffi`. `@deepseek-ai/cordis` and `@deepseek-ai/dsh-invariants` are listed as **peerDependencies** in `package.json` but are never imported by the runner or `AclSandbox` — they're only needed if the package is wired into DeepSeek's own `cordis`-based plugin host, which Agent8088 is not doing. Install with `--legacy-peer-deps` (or `--omit=peer`) to avoid pulling that framework in for no reason.
- **The runner's real argv contract** (from `lib/runner.js`, read directly):
  ```
  node runner.js --workspace <dir> --temp <dir> --mode <read-only|workspace-write>
                 [--write-sid <S-1-4-…> --temp-write-sid <S-1-4-…>] -- <argv...>
  ```
  `--write-sid`/`--temp-write-sid` are optional — when the caller (Agent8088) manages its own grant/revoke lifecycle it passes them and the runner only *verifies* them (`manageDacls: false`); when omitted, the runner self-manages a private temp child under `--temp` and revokes it on exit (`manageDacls: true`). **For the initial cut, omit `--write-sid`/`--temp-write-sid` and let the runner self-manage** — simpler, and the "materialize workspace ACE once, reuse across calls" optimization is a fast-follow once the basic path is proven (see Task 8).
  Failure contract: any runner-side failure prints `windows-acl-run: <detail>` to stderr and exits **127**. `_native_sandbox_unusable()` (`engine.py:3561-3569`) matches specific preflight-error substrings — add `"windows-acl-run:"` to `_NATIVE_SANDBOX_PREFLIGHT_ERRORS` (`engine.py:3508-3520`) so a Windows ACL runner refusal retries on Docker exactly like an `srt` preflight failure does today, instead of being misread as "the command ran and failed."

### The one real design problem: command-string translation

Every current call site builds a **POSIX shell command string** and hands it to the runtime via `--settings <file> -c <command>`:
```python
command = (f"cd {shlex.quote(str(cwd))} && "
           f"TMPDIR={shlex.quote(str(sandbox_tmp))} {command}")
```
(`engine.py:3616-3617`, `3813-3814`, `3839-3840`). `sandbox-runtime`'s own CLI docs its `-c` flag as "`run command string directly (like sh -c)`" (`~/.agent8088/runtime/.../dist/cli.js:115`) — and on Windows, `windows-sandbox-utils.js` (`~/.agent8088/runtime/.../windows-sandbox-utils.js:45-115`) resolves the *default* `binShell` to **`cmd.exe`** unless configured otherwise. `VAR=value command` env-prefix syntax is bash-only — `cmd.exe` has no equivalent, it would try to run a program literally named `TMPDIR=...`. **This means the existing Windows command construction was already broken independent of the `CreateProcessWithLogonW` bug** — it was simply never exercised because native sandbox has never successfully started on Windows.

The DSH runner makes this sharper, not easier: it has *zero* shell semantics — `-- <argv...>` execs the given program with the given args directly, no `&&`, no env-var assignment syntax, nothing. Task 3 below fixes this properly for the Windows path: pass `cmd.exe /d /s /c "<translated-command>"` as the runner's wrapped argv (so `cmd.exe` does the shell interpretation, not the runner), and build the translated string with `cmd.exe`-legal syntax (`cd /d`, `set "VAR=value"&`, not the bash equivalents) instead of reusing the POSIX string blindly.

---

## Trade-offs (confirmed against the live DSH README — document these in code comments, not just this plan)

1. **No network egress isolation.** `WRITE_RESTRICTED` intersects writes only — reads and sockets are unrestricted. Acceptable *today* only because `sandbox_allowed_domains` ships empty (`config.txt:85`). If that default ever changes, Windows silently loses an enforcement guarantee Linux/macOS still have — flag this loudly in `sandbox_status()` output for Windows (Task 6).
2. **No read-side confinement.** Credential paths (`denyRead` in `_sandbox_settings_data()`, `engine.py:3483-3491`) are enforced by `srt`'s settings JSON, which the DSH runner has no concept of — any caller-readable file is readable inside the sandbox on Windows. Out of scope for this plan; the DSH README's own suggested mitigation is a paired `icacls /deny` stamp — track as a fast-follow, don't build it here.
3. **Partial enforcement per DSH's own docs**: `Everyone`-ACL objects and NTFS hard-link aliases (this hits pnpm's content-addressable store) can bypass the write restriction.
4. **No piped-stdio capture for a sandboxed grandchild.** Named-pipe stdio (`spawn(..., {stdio:'pipe'})`) fails with EPERM inside a confined process; inherited/ignored stdio works fine. Since `_exec_process`/`_exec_sandbox_argv` on other platforms capture stdout/stderr via pipes at the *outer* (runner) process, not inside a grandchild, this should not affect Agent8088's own call pattern — confirm in Task 7's manual Windows test, don't just assume it.
5. **First grant on a large workspace does a one-time full ACL-tree propagation** (tens of seconds) — happens once per workspace per machine when self-managed (see argv contract note above), not per invocation.
6. **Version risk**: `0.1.0-rc.7` is a release candidate. Pin the exact version (no `^`/`~` range) the same way `SANDBOX_RUNTIME_VERSION` is pinned, and re-verify before ever bumping it.

Two things explicitly **out of scope** for this plan (call out, don't silently drop):
- Filing the upstream `CreateProcessWithLogonW`/`ERROR_ACCESS_DENIED` bug against `@anthropic-ai/sandbox-runtime` — cheap, worth doing, unrelated to this plan's execution.
- Keeping `srt-win.exe` reachable as a manual opt-in via `AGENT8088_SRT` (already the existing override mechanism at `engine.py:3320-3326` — no new code needed, just don't break it).

---

## Step-by-step plan

### Task 1: Add the Windows-only command-string translator

**Objective:** Solve the shell-translation problem once, in one place, so every call site can keep building "cd here, set this env var, run this" without knowing which platform it's on.

**Files:**
- Modify: `src/agent8088/engine.py` (new helper near `_native_sandbox_argv`, i.e. before line 3319)
- Test: `tests/test_native_sandbox_windows.py` (new)

**Step 1: Write failing test**
```python
# tests/test_native_sandbox_windows.py
import sys
import pytest
from agent8088 import engine


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only command translation")
def test_windows_shell_command_wraps_cmd_exe():
    argv = engine._native_sandbox_shell_argv("cd /d C:\\ws && set \"TMPDIR=C:\\tmp\"& echo hi")
    assert argv[0].lower().endswith("cmd.exe")
    assert argv[1:4] == ["/d", "/s", "/c"]
    assert argv[4] == "cd /d C:\\ws && set \"TMPDIR=C:\\tmp\"& echo hi"


def test_windows_shell_command_is_a_noop_off_windows(monkeypatch):
    monkeypatch.setattr(engine.sys, "platform", "linux")
    with pytest.raises(AttributeError):
        # guards against accidentally calling the win32-only helper elsewhere
        engine._native_sandbox_shell_argv("echo hi")
```
Note: the second test asserts the helper isn't accidentally invoked outside win32 branches — adjust once Task 3 wires the real call sites, since at that point the helper legitimately exists cross-platform but is only *called* under `sys.platform == "win32"`. Treat this as a placeholder to refine once Task 3 lands; the important assertion is the `cmd.exe /d /s /c` shape in the first test.

**Step 2: Run test to verify failure**

Run: `pytest tests/test_native_sandbox_windows.py -v`
Expected: FAIL — `AttributeError: module 'agent8088.engine' has no attribute '_native_sandbox_shell_argv'`

**Step 3: Write minimal implementation**

Add directly above `_native_sandbox_argv` (`engine.py:3319`):
```python
def _native_sandbox_shell_argv(command: str) -> list:
    """Wrap a shell command string for cmd.exe, since the Windows ACL runner
    execs argv directly and has no shell semantics of its own (no &&, no
    VAR=value env-prefix syntax) — cmd.exe does the interpreting instead.
    """
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    cmd_exe = str(Path(system_root) / "System32" / "cmd.exe")
    return [cmd_exe, "/d", "/s", "/c", command]
```

**Step 4: Run test to verify pass**

Run: `pytest tests/test_native_sandbox_windows.py -v`
Expected: PASS (Windows-specific assertion runs only under `sys.platform == "win32"`; on macOS/Linux CI it's skipped, not failed)

**Step 5: Commit**
```bash
git add src/agent8088/engine.py tests/test_native_sandbox_windows.py
git commit -m "feat(sandbox): add cmd.exe wrapper for Windows ACL runner commands"
```

---

### Task 2: Add SID helpers and the DSH runner path resolver

**Objective:** Give `_native_sandbox_argv()` a way to find `node` + the vendored DSH runner script, mirroring how it already finds `node` + `sandbox-runtime`'s `cli.js` (`engine.py:3327-3331`).

**Files:**
- Modify: `src/agent8088/engine.py`
- Test: `tests/test_native_sandbox_windows.py`

**Step 1: Write failing test**
```python
def test_dsh_runner_path_resolution(tmp_path, monkeypatch):
    fake_home = tmp_path / "agent8088-home"
    monkeypatch.setenv("AGENT8088_HOME", str(fake_home))
    runner = fake_home / "runtime" / "node_modules" / "@deepseek-ai" / "dsh-sandbox-windows-acl" / "lib" / "runner.js"
    runner.parent.mkdir(parents=True)
    runner.write_text("// stub")
    assert engine._dsh_runner_path() == runner
```

**Step 2: Run test to verify failure**

Run: `pytest tests/test_native_sandbox_windows.py::test_dsh_runner_path_resolution -v`
Expected: FAIL — `AttributeError: ... no attribute '_dsh_runner_path'`

**Step 3: Write minimal implementation**

Add near `_native_sandbox_argv` (`engine.py:3319`), after Task 1's helper:
```python
_DSH_SANDBOX_ACL_VERSION = "0.1.0-rc.7"  # pin exact — pre-1.0 package, no ranges


def _dsh_runner_path() -> Path:
    return (_agent_data_dir() / "runtime" / "node_modules" / "@deepseek-ai"
            / "dsh-sandbox-windows-acl" / "lib" / "runner.js")
```

**Step 4: Run test to verify pass**

Run: `pytest tests/test_native_sandbox_windows.py::test_dsh_runner_path_resolution -v`
Expected: PASS

**Step 5: Commit**
```bash
git add src/agent8088/engine.py tests/test_native_sandbox_windows.py
git commit -m "feat(sandbox): resolve the vendored DSH Windows ACL runner path"
```

---

### Task 3: Give `_native_sandbox_argv()` a Windows branch

**Objective:** On win32, return the DSH runner argv-prefix instead of the `srt` CLI path; on darwin/linux, behavior is unchanged.

**Files:**
- Modify: `src/agent8088/engine.py:3319-3333`
- Test: `tests/test_native_sandbox_windows.py`

**Step 1: Write failing test**
```python
def test_native_sandbox_argv_uses_dsh_runner_on_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(engine.sys, "platform", "win32")
    monkeypatch.delenv("AGENT8088_SRT", raising=False)
    fake_home = tmp_path / "agent8088-home"
    monkeypatch.setenv("AGENT8088_HOME", str(fake_home))
    runner = engine._dsh_runner_path()
    runner.parent.mkdir(parents=True)
    runner.write_text("// stub")
    monkeypatch.setattr(engine, "_which_executable",
                         lambda name: r"C:\node\node.exe" if name == "node" else None)

    argv = engine._native_sandbox_argv()

    assert argv[:2] == [r"C:\node\node.exe", str(runner)]
    assert "--workspace" in argv and "--mode" in argv
```

**Step 2: Run test to verify failure**

Run: `pytest tests/test_native_sandbox_windows.py::test_native_sandbox_argv_uses_dsh_runner_on_windows -v`
Expected: FAIL (current code returns the `srt` path shape, or `None` since the fake runner path doesn't match `srt`'s expected location)

**Step 3: Write minimal implementation**

Replace `engine.py:3319-3333`:
```python
def _native_sandbox_argv():
    override = os.environ.get("AGENT8088_SRT")
    if override:
        argv = shlex.split(override, posix=sys.platform != "win32")
        if sys.platform == "win32":
            argv = [part[1:-1] if len(part) > 1 and part[0] == part[-1] == '"'
                    else part for part in argv]
        return argv
    if sys.platform == "win32":
        node = _which_executable("node")
        runner = _dsh_runner_path()
        if not node or not runner.exists():
            return None
        return [node, str(runner)]
    cli = (_agent_data_dir() / "runtime" / "node_modules"
           / "@anthropic-ai" / "sandbox-runtime" / "dist" / "cli.js")
    node = _which_executable("node")
    if node and cli.exists():
        return [node, str(cli)]
    executable = _which_executable("srt")
    return [executable] if executable else None
```
Note: this returns only `[node, runner_path]` — the `--workspace/--temp/--mode/--/<argv>` suffix is per-call context (workspace path, readonly vs write, the actual wrapped command), so it belongs in the call sites (Task 4), not in this shared resolver. **Update the test above** once Task 4 lands to assert on the call-site-built full argv instead, since `_native_sandbox_argv()` alone can't include `--workspace` — adjust the test's last assertion to just check the `[node, runner]` prefix.

**Step 4: Run test to verify pass**

Run: `pytest tests/test_native_sandbox_windows.py -v`
Expected: PASS (after adjusting the assertion per the note above)

**Step 5: Commit**
```bash
git add src/agent8088/engine.py tests/test_native_sandbox_windows.py
git commit -m "feat(sandbox): resolve Windows ACL runner argv prefix on win32"
```

---

### Task 4: Wire the three call sites to build DSH-shaped argv on Windows

**Objective:** `_native_sandbox_ready()` (`engine.py:3590-3638`), `_exec_native_sandbox()` (`engine.py:3805-3817`), and `_exec_sandbox_argv()`'s native branch (`engine.py:3820-3850`) each currently build `runtime + ["--settings", str(settings), "-c", command]`. Add a Windows branch to each that instead builds `runtime + ["--workspace", str(cwd), "--temp", str(sandbox_tmp), "--mode", mode, "--", *_native_sandbox_shell_argv(command)]`.

**Files:**
- Modify: `src/agent8088/engine.py:3590-3638, 3805-3817, 3820-3850`
- Test: `tests/test_native_sandbox_windows.py`

**Step 1: Write failing test**
```python
def test_exec_native_sandbox_builds_dsh_argv_on_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(engine.sys, "platform", "win32")
    monkeypatch.setenv("AGENT8088_HOME", str(tmp_path / "home"))
    runner = engine._dsh_runner_path()
    runner.parent.mkdir(parents=True)
    runner.write_text("// stub")
    monkeypatch.setattr(engine, "_which_executable",
                         lambda name: r"C:\node\node.exe" if name == "node" else None)
    captured = {}

    def fake_exec_process(argv, timeout):
        captured["argv"] = argv
        return "ok"

    monkeypatch.setattr(engine, "_exec_process", fake_exec_process)

    engine._exec_native_sandbox("echo hi", timeout=10, cwd=tmp_path)

    argv = captured["argv"]
    assert "--workspace" in argv
    assert "--mode" in argv
    assert argv[argv.index("--mode") + 1] == "workspace-write"
    assert argv[-1] == "echo hi"  # the wrapped command is the final cmd.exe arg
```

**Step 2: Run test to verify failure**

Run: `pytest tests/test_native_sandbox_windows.py::test_exec_native_sandbox_builds_dsh_argv_on_windows -v`
Expected: FAIL — current code still calls `["--settings", ..., "-c", command]` unconditionally

**Step 3: Write minimal implementation**

In `_exec_native_sandbox` (`engine.py:3805-3817`), replace the body with a platform branch:
```python
def _exec_native_sandbox(command: str, timeout: int, cwd: Path | None = None,
                         readonly: bool = False) -> str:
    argv = _native_sandbox_argv()
    if not argv:
        return "Native sandbox runtime is unavailable."
    cwd = (cwd or ARTIFACTS_ROOT).resolve()
    sandbox_tmp = (_agent_data_dir() / "sandbox-tmp").resolve()
    if sys.platform == "win32":
        mode = "read-only" if readonly else "workspace-write"
        wrapped = _native_sandbox_shell_argv(command)
        full_argv = argv + ["--workspace", str(cwd), "--temp", str(sandbox_tmp),
                            "--mode", mode, "--"] + wrapped
        return _exec_process(full_argv, timeout=timeout)
    settings = _write_sandbox_settings(readonly, cwd)
    command = (f"cd {shlex.quote(str(cwd))} && "
               f"TMPDIR={shlex.quote(str(sandbox_tmp))} {command}")
    return _exec_process(
        argv + ["--settings", str(settings), "-c", command], timeout=timeout
    )
```
Apply the equivalent branch to `_native_sandbox_ready` (`engine.py:3590-3638`, the block building `command` at lines 3616-3620) and `_exec_sandbox_argv`'s native branch (`engine.py:3833-3847`). Keep `sandbox_tmp.mkdir` behavior identical — only the argv shape differs by platform.

**Step 4: Run test to verify pass**

Run: `pytest tests/test_native_sandbox_windows.py -v`
Expected: PASS

**Step 5: Commit**
```bash
git add src/agent8088/engine.py tests/test_native_sandbox_windows.py
git commit -m "feat(sandbox): build Windows ACL runner argv at all three native call sites"
```

---

### Task 5: Give `_native_sandbox_missing_requirements()` real Windows checks

**Objective:** Replace the current `else: required = ()` no-op (`engine.py:3343-3344`) with an actual check for the DSH runner file + `koffi`'s native addon presence, so `sandbox_status()` reports something actionable instead of silently treating Windows as "nothing extra needed."

**Files:**
- Modify: `src/agent8088/engine.py:3336-3345`
- Test: `tests/test_native_sandbox_windows.py`

**Step 1: Write failing test**
```python
def test_missing_requirements_flags_absent_koffi_addon(tmp_path, monkeypatch):
    monkeypatch.setattr(engine.sys, "platform", "win32")
    monkeypatch.setenv("AGENT8088_HOME", str(tmp_path / "home"))
    runner = engine._dsh_runner_path()
    runner.parent.mkdir(parents=True)
    runner.write_text("// stub")
    monkeypatch.setattr(engine, "_which_executable",
                         lambda name: r"C:\node\node.exe" if name == "node" else None)
    # koffi's build/ dir absent -> should be reported missing
    missing = engine._native_sandbox_missing_requirements()
    assert "koffi native addon" in missing
```

**Step 2: Run test to verify failure**

Run: `pytest tests/test_native_sandbox_windows.py::test_missing_requirements_flags_absent_koffi_addon -v`
Expected: FAIL — current code returns `[]` unconditionally on win32 once `_native_sandbox_argv()` succeeds

**Step 3: Write minimal implementation**

Replace `engine.py:3336-3345`:
```python
def _native_sandbox_missing_requirements() -> list:
    if not _native_sandbox_argv():
        return ["sandbox-runtime"]
    if sys.platform == "darwin":
        required = ("sandbox-exec", "rg")
    elif sys.platform.startswith("linux"):
        required = ("bwrap", "socat", "rg")
    elif sys.platform == "win32":
        missing = []
        koffi_dir = (_agent_data_dir() / "runtime" / "node_modules" / "koffi")
        if not koffi_dir.is_dir():
            missing.append("koffi native addon")
        return missing
    else:
        required = ()
    return [command for command in required if not shutil.which(command)]
```

**Step 4: Run test to verify pass**

Run: `pytest tests/test_native_sandbox_windows.py -v`
Expected: PASS

**Step 5: Commit**
```bash
git add src/agent8088/engine.py tests/test_native_sandbox_windows.py
git commit -m "feat(sandbox): check for the koffi addon in Windows requirement checks"
```

---

### Task 6: Update `_native_sandbox_repair_hint()` and the preflight-error list for Windows

**Objective:** Stop telling Windows users to check seclogon/antivirus/elevation (a diagnosis for a bug this plan removes); point them at `--sandbox-setup` for the ACL sandbox instead. Also teach `_native_sandbox_unusable()` to recognize a DSH runner refusal.

**Files:**
- Modify: `src/agent8088/engine.py:3508-3520` (`_NATIVE_SANDBOX_PREFLIGHT_ERRORS`)
- Modify: `src/agent8088/engine.py:3523-3558` (`_native_sandbox_repair_hint`)
- Test: `tests/test_native_sandbox_windows.py`

**Step 1: Write failing test**
```python
def test_repair_hint_windows_points_at_sandbox_setup():
    hint = engine._native_sandbox_repair_hint("windows-acl-run: workspace grant failed")
    assert "--sandbox-setup" in hint
    assert "seclogon" not in hint.lower()


def test_windows_acl_runner_failure_is_recognized_as_preflight():
    assert engine._native_sandbox_unusable("windows-acl-run: bad --workspace argument")
```

**Step 2: Run test to verify failure**

Run: `pytest tests/test_native_sandbox_windows.py -k repair_hint -v`
Expected: FAIL — current hint text still mentions seclogon for any `"Access is denied"`/`"CreateProcessWithLogonW"` match, and `"windows-acl-run:"` isn't in `_NATIVE_SANDBOX_PREFLIGHT_ERRORS` yet

**Step 3: Write minimal implementation**

Add `"windows-acl-run:"` to the tuple at `engine.py:3508-3520`:
```python
_NATIVE_SANDBOX_PREFLIGHT_ERRORS = (
    "Native sandbox runtime is unavailable.",
    "WFP egress fence could not be verified",
    "CreateProcessWithLogonW",
    "Secondary Logon service",
    "srt-win: error:",
    "windows-acl-run:",
    "bwrap: No permissions to create new namespace",
    "bwrap: Creating new namespace failed",
    "bwrap: Can't mount proc",
    "apply-seccomp:",
    "sandbox-exec: sandbox_init:",
    "sandbox-exec: sandbox_apply:",
)
```
In `_native_sandbox_repair_hint` (`engine.py:3523-3558`), replace the `CreateProcessWithLogonW`/seclogon branch with:
```python
    checks = ""
    if "windows-acl-run:" in text:
        checks = ("The Windows ACL sandbox runner refused to start. Run "
                  "`agent8088 --sandbox-setup` to reinstall it.")
    elif "CreateProcessWithLogonW" in text or "Access is denied" in text:
        checks = ("Windows refused the spawn. Run `agent8088 --sandbox-setup` "
                  "to reinstall the native sandbox.")
```
(Keep the rest of the function — the docstring's history note about the seclogon paragraph can stay as historical context, or be trimmed; don't over-edit prose that isn't load-bearing for this task.)

**Step 4: Run test to verify pass**

Run: `pytest tests/test_native_sandbox_windows.py -v`
Expected: PASS

**Step 5: Commit**
```bash
git add src/agent8088/engine.py tests/test_native_sandbox_windows.py
git commit -m "fix(sandbox): point Windows repair hint at --sandbox-setup, not seclogon"
```

---

### Task 7: Replace `install_native_sandbox()`'s Windows provisioning branch

**Objective:** Stop running `node cli.js windows-install` (the `srt-sandbox`-account step). Instead npm-install `@deepseek-ai/dsh-sandbox-windows-acl` + its `koffi` dependency into the same `runtime/` directory already used for `@anthropic-ai/sandbox-runtime`.

**Files:**
- Modify: `src/agent8088/engine.py:4111-4151`
- Test: `tests/test_install_native_sandbox_windows.py` (new)

**Step 1: Write failing test**
```python
# tests/test_install_native_sandbox_windows.py
import sys
import pytest
from agent8088 import engine


def test_windows_install_runs_npm_for_dsh_package(tmp_path, monkeypatch):
    monkeypatch.setattr(engine.sys, "platform", "win32")
    monkeypatch.setenv("AGENT8088_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(engine, "_which_executable",
                         lambda name: {"node": "node", "npm": "npm"}.get(name))
    monkeypatch.setattr(engine.subprocess, "run",
                         lambda *a, **k: type("R", (), {"stdout": "v20.11.0\n"})())
    calls = []

    def fake_exec_process(argv, timeout):
        calls.append(argv)
        return "ok"

    monkeypatch.setattr(engine, "_exec_process", fake_exec_process)
    monkeypatch.setattr(engine, "_native_sandbox_missing_requirements", lambda: [])
    monkeypatch.setattr(engine, "_native_sandbox_ready", lambda *a, **k: True)

    engine.install_native_sandbox()

    npm_call = calls[0]
    assert "@deepseek-ai/dsh-sandbox-windows-acl@" in npm_call[-1]
    assert not any("windows-install" in str(part) for call in calls for part in call)
```

**Step 2: Run test to verify failure**

Run: `pytest tests/test_install_native_sandbox_windows.py -v`
Expected: FAIL — current code npm-installs `@anthropic-ai/sandbox-runtime` and then runs `windows-install`

**Step 3: Write minimal implementation**

Replace `engine.py:4111-4151`:
```python
def install_native_sandbox() -> str:
    node = _which_executable("node")
    npm = _which_executable("npm")
    if not node or not npm:
        return "Node.js 20.11 or newer is required to install the native sandbox runtime."
    try:
        version = subprocess.run(
            [node, "--version"], capture_output=True, text=True, timeout=10
        ).stdout.strip().lstrip("v")
        major, minor = (int(part) for part in version.split(".")[:2])
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return "Could not determine the installed Node.js version."
    if (major, minor) < (20, 11):
        return f"Node.js 20.11 or newer is required (found {version})."

    runtime_dir = _agent_data_dir() / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        result = _exec_process([
            npm, "install", "--prefix", str(runtime_dir), "--no-audit", "--no-fund",
            "--legacy-peer-deps",
            f"@deepseek-ai/dsh-sandbox-windows-acl@{_DSH_SANDBOX_ACL_VERSION}",
        ], timeout=300)
    else:
        result = _exec_process([
            npm, "install", "--prefix", str(runtime_dir), "--no-audit", "--no-fund",
            f"@anthropic-ai/sandbox-runtime@{SANDBOX_RUNTIME_VERSION}",
        ], timeout=300)
    if "exited with status" in result or "timed out" in result:
        return result
    missing = _native_sandbox_missing_requirements()
    if missing:
        return (
            f"Native sandbox runtime installed. "
            f"Install the remaining OS packages: {', '.join(missing)}."
        )
    if not _native_sandbox_ready(ARTIFACTS_ROOT, quiet=True):
        return (f"Native sandbox runtime installed but could not "
                f"be verified. {_native_sandbox_repair_hint(_native_sandbox_failure)} "
                "Docker will be used when available.")
    return "Native sandbox runtime installed and verified."
```
Note: the returned strings previously interpolated `SANDBOX_RUNTIME_VERSION` even for the message — since Windows now installs a different package/version, this drops the version number from the generic messages rather than showing the wrong one. If a future task wants the DSH version surfaced too, that's a small follow-up, not required for correctness here.

**Step 4: Run test to verify pass**

Run: `pytest tests/test_install_native_sandbox_windows.py -v`
Expected: PASS

**Step 5: Commit**
```bash
git add src/agent8088/engine.py tests/test_install_native_sandbox_windows.py
git commit -m "feat(sandbox): install DSH Windows ACL package instead of srt-win"
```

---

### Task 8: Re-enable `install.ps1`'s Windows sandbox setup stage

**Objective:** Make `Install-Native-Sandbox` actually call `agent8088 --sandbox-setup` again now that the Windows path no longer needs UAC or a second account.

**Files:**
- Modify: `install.ps1:952-978`

**Step 1: Manual verification step (no automated test — this is a PowerShell install stage; validate via Task 9's real-machine checklist)**

Replace `install.ps1:952-978`:
```powershell
# ----------------------------------------------------------------------------
# Stage 5d: Native sandbox runtime
# ----------------------------------------------------------------------------
# Uses the DSH Windows ACL sandbox backend (restricted-token + NTFS ACL write
# confinement) instead of srt-win.exe's CreateProcessWithLogonW spawn, which
# was refused with ERROR_ACCESS_DENIED with no audit trail on at least one
# machine (see git history around commit 230c6ff for the original disable).
# No UAC prompt, no second account — safe to run unattended during install.
function Install-Native-Sandbox {
    $agentExe = Join-Path $InstallDir "venv\Scripts\agent8088.exe"
    if (-not (Test-Path -LiteralPath $agentExe)) {
        Write-Warn "Agent8088 executable not found - skipping native sandbox setup"
        return
    }
    Write-Info "Installing native sandbox (Windows ACL backend)..."
    $result = & $agentExe --sandbox-setup 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Success "Native sandbox installed and verified"
    } else {
        Write-Warn "Native sandbox setup did not verify - Docker will be used for sandboxing if available"
        Write-Warn "$result"
    }
}
```
Find the call site (`grep -n "Install-Native-Sandbox" install.ps1`) and confirm it's still invoked at the same point in the install sequence — no change needed there since the function name/call is unchanged, only its body.

**Step 2: Commit**
```bash
git add install.ps1
git commit -m "fix(install): re-enable Windows native sandbox setup with the ACL backend"
```

---

### Task 9: Windows branch for `scripts/verify_native_sandbox.py`

**Objective:** Give win32 the same contract probe darwin/linux already get: workspace write allowed, network egress unaffected (documented as *not* blocked — see below), timeout enforced, fallback refuses unsandboxed execution. Credential-read-denial is explicitly **not** asserted for Windows (Trade-off #2) — assert the opposite, so a future regression that silently "fixes" this without updating the trade-off doc is caught, not celebrated silently.

**Files:**
- Modify: `scripts/verify_native_sandbox.py:43-51` (`_require_prerequisites`)
- Modify: `scripts/verify_native_sandbox.py:54-94` (`_child`)

**Step 1: Manual verification step (this script IS the test — no separate pytest wrapper; it must run against a real Windows machine, see Task 10)**

In `_require_prerequisites` (`scripts/verify_native_sandbox.py:43-51`), Windows needs no extra OS binary checks (same as today — the DSH runner + koffi check already lives in `engine._native_sandbox_missing_requirements()`, which this function already calls indirectly via `_runtime_argv()`... actually it duplicates the logic locally). Update the local dict:
```python
    required = {"darwin": ("sandbox-exec", "rg"), "linux": ("bwrap", "socat", "rg")}.get(sys.platform, ())
```
stays as-is (win32 correctly falls through to `()` — no separate OS binaries needed for the DSH backend beyond what's already vendored under `runtime/`).

In `_child` (`scripts/verify_native_sandbox.py:54-94`), split the credential-read assertion by platform:
```python
    read_result = engine._exec_sandbox_command(f"cat {shlex.quote(str(secret))}")
    if sys.platform == "win32":
        if MARKER not in read_result:
            _fail("Windows ACL sandbox unexpectedly blocked a credential-path read "
                  "(expected: reads are NOT confined on this backend — see plan "
                  "trade-off #2; if this now passes, the trade-off note is stale)")
    elif MARKER in read_result:
        _fail("native sandbox read a protected credential path")
```
Leave the write-protection assertion (`engine._exec_sandbox_command(f"printf altered > {shlex.quote(str(secret))}")`) unchanged — write confinement is real on Windows too, since `secret`'s parent (`home/.ssh/`) sits outside the granted workspace root.

The network-egress assertion (`engine.py` verify script lines ~76-80) should also branch: Windows is expected to *succeed* at reaching the network (no egress fence), so assert the opposite of darwin/linux:
```python
    network_result = engine._exec_sandbox_command(
        shlex.join([sys.executable, "-c", network_code]), timeout=6)
    if sys.platform == "win32":
        pass  # no egress fence on this backend — intentionally not asserted either way
    elif "A8088_NET_OK" in network_result:
        _fail(f"native sandbox reached the network without an allowlist: {network_result[:1000]}")
```

**Step 2: Manual run against a real Windows machine (see Task 10 — cannot be verified from this dev environment)**

Run: `python scripts/verify_native_sandbox.py`
Expected on Windows: `PASS: native sandbox enforced workspace, credential, network, timeout, and fallback boundaries` (with credential-read and network-egress now correctly reflecting the documented Windows trade-offs instead of failing on them)

**Step 3: Commit**
```bash
git add scripts/verify_native_sandbox.py
git commit -m "test(sandbox): adapt verify_native_sandbox.py for Windows ACL trade-offs"
```

---

### Task 10: Real-machine validation checklist (cannot be done from this dev environment)

**Objective:** Everything above is unit-testable on any OS by mocking `sys.platform`/`_which_executable`/`_exec_process`, but the actual Win32 API behavior (`CreateRestrictedToken`, ACL grants, `cmd.exe /d /s /c` translation correctness) can only be proven on a real Windows machine. Document this explicitly rather than claiming false confidence.

**Checklist for whoever has Windows hardware:**
1. `git checkout` this branch, run `install.ps1` fresh, confirm Stage 5d prints "Native sandbox installed and verified" (not a skip).
2. `agent8088 --sandbox-setup` a second time — confirm it's idempotent (the DSH README notes `grantWrite` skips `SetNamedSecurityInfoW` when the exact ACE already stands).
3. `python scripts/verify_native_sandbox.py` — confirm the PASS line from Task 9.
4. Run a real Agent8088 session, issue a shell-tool command that does `cd`+env-var+chained-command (e.g. something equivalent to the `TMPDIR=... command` shape) — confirm the `cmd.exe /d /s /c` translation from Task 1 actually executes it correctly, not just that the argv shape matches in a mocked test.
5. Confirm `sandbox_status()` reports `"native"`, not falling back to `"docker"`.
6. Test the fallback path deliberately: rename/remove the vendored `runner.js`, confirm `sandbox_status()` correctly reports `docker` (or `unavailable` with Docker off) rather than crashing.
7. Time the first workspace grant on a large repo (tens-of-seconds propagation per the DSH docs) — confirm it doesn't trip Agent8088's own command timeout on first use.

This task has no code to write — it's a sign-off gate before calling this plan "done." Do not mark Tasks 1-9 as fully validated until this checklist has run on real Windows hardware at least once.

---

## Files likely to change (summary)

- `src/agent8088/engine.py` — `_native_sandbox_argv`, `_native_sandbox_missing_requirements`, `_native_sandbox_repair_hint`, `_NATIVE_SANDBOX_PREFLIGHT_ERRORS`, `_native_sandbox_ready`, `_exec_native_sandbox`, `_exec_sandbox_argv`, `install_native_sandbox`, plus two new helpers (`_native_sandbox_shell_argv`, `_dsh_runner_path`) and one new constant (`_DSH_SANDBOX_ACL_VERSION`)
- `install.ps1` — `Install-Native-Sandbox` (`install.ps1:952-978`)
- `scripts/verify_native_sandbox.py` — `_child`, platform branches for credential-read and network-egress assertions
- New: `tests/test_native_sandbox_windows.py`, `tests/test_install_native_sandbox_windows.py`

## Tests / validation

- `pytest tests/test_native_sandbox_windows.py tests/test_install_native_sandbox_windows.py -v` — runs cross-platform (Windows-specific assertions activate via `monkeypatch.setattr(engine.sys, "platform", "win32")`, so CI on macOS/Linux still exercises the Windows code paths through mocking).
- Full suite: `pytest tests/ -v` — confirm zero regressions on the existing darwin/linux sandbox paths (Tasks 3-4's platform branches must leave the non-Windows `else` branches byte-for-byte identical to today).
- `scripts/verify_native_sandbox.py` — real end-to-end probe, Windows-hardware-only per Task 10.

## Risks, trade-offs, and open questions

See the "Trade-offs" section above for the six confirmed limitations (no egress isolation, no read confinement, Everyone/hard-link partial enforcement, no piped-stdio-for-grandchildren, one-time ACL propagation cost, pre-1.0 package version). Open questions for whoever executes this plan:

1. **Should `--write-sid`/`--temp-write-sid` (the caller-managed grant lifecycle) be adopted in this initial cut, or deferred?** This plan defers it (Task 4 lets the runner self-manage a temp child under `--temp`) for simplicity. The trade-off: every invocation re-derives a temp SID/directory instead of reusing one materialized workspace ACE across a session. Revisit once Task 10's real-machine timing data shows whether the self-managed path's overhead is acceptable.
2. **Should the DSH version be surfaced in `install_native_sandbox()`'s return strings** the way `SANDBOX_RUNTIME_VERSION` is today? Dropped in Task 7 for simplicity; small follow-up if wanted.
3. **`cmd.exe` as the fixed shell target (Task 1)** — the DSH README documents `pwsh`/`powershell` as alternative `binShell` options for `sandbox-runtime`'s own settings, but the DSH ACL runner doesn't have a `binShell` concept at all (it just execs whatever argv follows `--`). This plan hard-codes `cmd.exe /d /s /c` as that argv. If any existing Agent8088 command string relies on PowerShell-specific syntax rather than portable `cmd.exe`-compatible syntax, it will break under this translation — audit actual command strings sandboxed tools send today as part of Task 10's manual validation, not assumed away here.
