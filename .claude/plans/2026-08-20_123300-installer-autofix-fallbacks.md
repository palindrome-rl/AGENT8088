# Installer Auto-Fixes & Fallbacks Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Close the specific, evidence-backed gaps between Agent8088's install/runtime
reliability and the documented practices of Hermes Agent and OpenClaw — TLS/protocol
pinning on the bootstrap command, a download-tool fallback for hosts without curl, a
`doctor --fix` self-repair path, and a shareable diagnostic bundle — so a failure on a
machine that has never run Agent8088 before is either auto-repaired or reported with
one specific, actionable line, the same standard the Aug 19 installer-hardening plan
already applied to timeouts and proxies.

**Architecture:** Five independent parts, each safe to land alone:
`A` pins TLS/protocol on the documented one-liners (OpenClaw) →
`B` adds a wget fallback for the one unguarded internal download (Aider) →
`C` adds `agent8088 doctor --fix` self-repair for the one known-broken-but-not-yet-
self-healing failure mode (Hermes) →
`D` adds `agent8088 dump` for remote-diagnosable bug reports (Hermes) →
`E` publishes the compatibility matrix that today only lives in a planning doc (Hermes).

**Tech stack:** bash 3.2+, PowerShell 5.1/7.x, Python 3.10+, pytest. No new
dependencies — everything here is stdlib (`subprocess`, `shutil`, `platform`) plus
functions this codebase already has (`collect_secret_values`, `_ddgs_installed`,
`sandbox_status`).

---

## Context — what's already solid, and what genuinely isn't

This repo's `.claude/plans/2026-08-19_143000-universal-installer-hardening.md` already
landed (verified against `development@899f17e` before writing this plan):
timeout machinery on every network stage, proxy env-var normalization, TLS 1.2 forced
in `install.ps1` before the terminal gate, the bash-3.2 portability lint
(`scripts/check_installer_portability.sh`), and — importantly — **venv self-repair is
already implemented**: `install.sh:723-737` detects a venv left over from an
interrupted run (`uv venv --allow-existing` fails, or `$_py` isn't executable) and
rebuilds it automatically with `--clear`, logging *"Existing virtualenv is not usable —
rebuilding it"*. Do not re-implement this — it is why this plan does not have a "Part
B: auto-clean broken venv" task.

The ddgs mis-detection bug the Aug 19 plan flagged as still-open
(`_ddgs_installed()` only checking `find_spec`) is **also already fixed** —
`web_search.py:548-577` now actually imports the `ddgs` symbol, not just checks for the
module's presence on disk. So `doctor`/`search doctor` correctly *report* a broken ddgs
install today. What's missing is the next step: **reporting it is not the same as
fixing it**, and neither `/doctor` nor `/search doctor` acts on that report. That gap —
detect-but-don't-repair — is exactly what Hermes's `hermes doctor --fix` closes, and is
Part C below.

What's genuinely missing, found via Context7 docs research on Hermes Agent
(`/nousresearch/hermes-agent`) and OpenClaw (`/openclaw/openclaw.ai`):

| Gap | Evidence | Part |
|---|---|---|
| No TLS/protocol pinning on the documented bootstrap curl command | README.md:51 is bare `curl -fsSL`; OpenClaw's docs (`docs/installers.md`) pin `curl -fsSL --proto '=https' --tlsv1.2 ...` | A |
| No fallback when curl is unavailable | `grep -c wget install.sh` → 0; Aider's docs offer `wget -qO- https://aider.chat/install.sh \| sh` as an explicit fallback when curl is missing | A, B |
| No auto-repair action anywhere in the CLI | `cmd_doctor` (`cli.py:2492`) only reports; Hermes's `cli-commands.md` describes `hermes doctor --fix` as attempting "automatic repairs where possible" | C |
| No shareable diagnostic bundle | Nothing produces a dump file today; Hermes ships `hermes dump` specifically "for sharing plain-text debug summaries", distinct from `doctor` (repair) and `status` (overview) | D |
| No published compatibility matrix | The Aug 19 plan's compatibility contract table lives only in `.claude/plans/`, not anywhere a stranger would see it before installing; Hermes publishes this as `platform-support.md` | E |

**Explicitly out of scope, and why:** OpenClaw's separate `install-cli.sh` /
`--non-interactive` variant is not proposed here because Agent8088 already has the
equivalent (`--skip-setup`, and setup auto-skips when non-interactive —
`install.sh:1355-1359`). Sandbox auto-fallback (native → Docker → unavailable) is not
proposed as a `doctor --fix` target because it already happens automatically in
`_resolve_sandbox_backend()`/`sandbox_status()` (`engine.py:3436-3490`) — there is
nothing left to repair there, only to report, which it already does.

---

# Part A — Pin TLS/protocol on the documented bootstrap commands

### Task A1: Add `--proto '=https' --tlsv1.2` to the README's curl one-liner

**Files:** Modify: `README.md:51`

**Step 1: Replace the macOS/Linux/WSL2 install command**

```diff
-curl -fsSL https://raw.githubusercontent.com/RT-Internal-DS/Agent8088-Features-added/development/install.sh | AGENT8088_BRANCH=development bash
+curl -fsSL --proto '=https' --tlsv1.2 https://raw.githubusercontent.com/RT-Internal-DS/Agent8088-Features-added/development/install.sh | AGENT8088_BRANCH=development bash
```

`--proto '=https'` refuses a redirect to plain `http://`; `--tlsv1.2` refuses a
downgrade. Both are free — curl has supported them for a decade — and this is exactly
the class of failure that only shows up on someone else's network (captive portal,
stale corporate TLS-terminating proxy) and never on a dev machine already holding a
warm connection to GitHub.

**Step 2: Verify**

```bash
grep -n "proto.*https.*tlsv1.2" README.md
```
Expected: one match, on the line just edited.

**Step 3: Commit**

```bash
git add README.md
git commit -m "docs: pin curl to https+TLS1.2 on the documented install command"
```

---

### Task A2: Match the same flags in `install.sh`'s own usage text

**Files:** Modify: `install.sh:6`, `install.sh:1327`

These two lines are the script's self-documented invocation (shown by `--help` and in
the final "how to update" summary) and should not drift from what README says to run.

**Step 1: Update the usage header (`:6`)**

```diff
-#   curl -fsSL https://<YOUR-URL>/install.sh | bash
+#   curl -fsSL --proto '=https' --tlsv1.2 https://<YOUR-URL>/install.sh | bash
```

**Step 2: Update the update-hint line (`:1327`)**

```diff
-    echo "  Update: AGENT8088_BRANCH=$BRANCH curl -fsSL https://raw.githubusercontent.com/tayyabimam1/Agent8088-Features-added/$BRANCH/install.sh | bash"
+    echo "  Update: AGENT8088_BRANCH=$BRANCH curl -fsSL --proto '=https' --tlsv1.2 https://raw.githubusercontent.com/tayyabimam1/Agent8088-Features-added/$BRANCH/install.sh | bash"
```

**Step 3: Verify**

```bash
bash -n install.sh && echo "syntax OK"
grep -c "proto '=https' --tlsv1.2" install.sh
```
Expected: `syntax OK`, count `2`.

**Step 4: Commit**

```bash
git add install.sh
git commit -m "docs(install): match README's https+TLS1.2 pin in install.sh's own usage text"
```

---

# Part B — wget fallback for the one unguarded internal download

Only one place inside `install.sh` performs a raw, unconditional `curl` call without
already being covered by the bash-3.2 lint's scope: the portable Node.js tarball
download at `install.sh:963` (needed only for the optional WhatsApp bridge). Everything
else — the outer bootstrap fetch — is the user's own curl/PowerShell invocation and out
of the script's control; this is the one spot the script can genuinely make more
resilient.

### Task B1: Add a `_download_file` helper that tries curl, falls back to wget

**Files:** Modify: `install.sh` — insert after `CURL_STALL_FLAGS` (`:108`)

**Step 1: Insert the helper**

```bash
# Download one URL to one path, trying curl first and falling back to wget. Some
# minimal base images (notably a few Alpine variants used in CI containers) ship
# wget but not curl, or vice versa -- Aider's installer documents the same
# curl-then-wget fallback for exactly this reason. Returns the underlying tool's
# exit code; the caller already treats a non-zero/timeout result as "this optional
# stage didn't work" rather than a hard failure.
_download_file() {
    local _dl_url="$1" _dl_out="$2" _dl_timeout="$3"
    if command -v curl >/dev/null 2>&1; then
        run_with_timeout "$_dl_timeout" curl -fsSL "${CURL_STALL_FLAGS[@]}" "$_dl_url" -o "$_dl_out" 2>/dev/null
        return $?
    fi
    if command -v wget >/dev/null 2>&1; then
        # wget's nearest equivalents: --timeout bounds a stalled read (curl's
        # --speed-time), --tries=1 avoids wget's own silent retry loop stacking on
        # top of run_with_timeout's wall clock.
        run_with_timeout "$_dl_timeout" wget -q --timeout=30 --tries=1 -O "$_dl_out" "$_dl_url" 2>/dev/null
        return $?
    fi
    log_warn "Neither curl nor wget is available - cannot download $_dl_url"
    return 1
}
```

**Step 2: Verify**

```bash
bash -n install.sh && echo "syntax OK"
grep -c "_download_file" install.sh
```
Expected: `syntax OK`, count ≥ 1 (the definition itself).

**Step 3: Commit**

```bash
git add install.sh
git commit -m "feat(install): add curl-or-wget download helper"
```

---

### Task B2: Route the Node tarball download through it

**Files:** Modify: `install.sh:963`

**Step 1: Replace the bare curl call**

```diff
-            if run_with_timeout "$T_NODE_DL" curl -fsSL "${CURL_STALL_FLAGS[@]}" "$_url" -o "$_tmp" 2>/dev/null; then
+            if _download_file "$_url" "$_tmp" "$T_NODE_DL"; then
```

**Step 2: Verify no bare curl remains for this stage**

```bash
grep -n '"$_url" -o "$_tmp"' install.sh
```
Expected: no output (the line now reads `_download_file "$_url" "$_tmp" "$T_NODE_DL"`).

**Step 3: Commit**

```bash
git add install.sh
git commit -m "fix(install): fall back to wget for the portable Node download"
```

---

### Task B3: Test the fallback in isolation

**Files:** Create: `tests/test_installer_download_fallback.py`

Follows the existing convention in `tests/test_installer_timeouts.py`: extract one
function from `install.sh` with a small shell harness and run it directly, rather than
performing a real install.

**Step 1: Write the test**

```python
"""install.sh's curl-or-wget download fallback, exercised in isolation.

Mirrors the extraction convention in test_installer_timeouts.py: pull just
run_with_timeout + _download_file out of install.sh and run them under a bare bash,
with `curl`/`wget` shadowed by a PATH-first stub so the test never touches the
network.
"""
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _extract(*names: str) -> str:
    source = (ROOT / "install.sh").read_text(encoding="utf-8")
    blocks = []
    for name in names:
        import re
        match = re.search(rf"(?ms)^{re.escape(name)}\(\) \{{.*?^\}}$", source)
        assert match, f"function not found in install.sh: {name}"
        blocks.append(match.group(0))
    return "\n".join(blocks)


def _write_stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _run(fake_bin: Path, extra_script: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    script = (
        'CURL_STALL_FLAGS=(--connect-timeout 20)\n'
        'run_with_timeout() { local secs="$1"; shift; "$@"; }\n'
        + _extract("_download_file")
        + "\n"
        + extra_script
    )
    return subprocess.run(["bash", "-c", script], env=env,
                           capture_output=True, text=True, timeout=10)


def test_uses_curl_when_present(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_stub(fake_bin, "curl", 'echo "curl called: $*" >&2; exit 0')
    result = _run(fake_bin, '_download_file "http://x" "/tmp/out" 5; echo "rc=$?"')
    assert "curl called" in result.stderr
    assert "rc=0" in result.stdout


def test_falls_back_to_wget_when_curl_absent(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_stub(fake_bin, "wget", 'echo "wget called: $*" >&2; exit 0')
    # An empty PATH-only bin dir has no curl, so `command -v curl` fails naturally.
    result = _run(fake_bin, '_download_file "http://x" "/tmp/out" 5; echo "rc=$?"')
    assert "wget called" in result.stderr
    assert "rc=0" in result.stdout


def test_warns_when_neither_tool_exists(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    result = _run(fake_bin, '_download_file "http://x" "/tmp/out" 5 2>&1; echo "rc=$?"')
    assert "Neither curl nor wget" in result.stdout
    assert "rc=1" in result.stdout
```

**Step 2: Run it**

```bash
python3 -m pytest tests/test_installer_download_fallback.py -v
```
Expected: `3 passed`. If `command -v curl`/`wget` still finds the real system binary
inside the sandboxed `PATH`, double-check `fake_bin` is prepended (not appended) — the
tests rely on `command -v` finding the stub or nothing, in that order.

**Step 3: Commit**

```bash
git add tests/test_installer_download_fallback.py
git commit -m "test(install): cover the curl-or-wget download fallback"
```

---

# Part C — `agent8088 doctor --fix` self-repair

Scoped to the one failure mode this codebase can already *detect* precisely but does
not yet *repair*: a core dependency (today, specifically `ddgs`) whose files are on
disk but fails to import — the exact "detection says yes, reality says no" case
`_ddgs_installed()` (`web_search.py:548`) exists to catch.

### Task C1: Add a package-reinstall helper

**Files:** Modify: `src/agent8088/cli.py` — insert before `cmd_doctor` (`:2492`)

**Step 1: Write the helper**

```python
def _reinstall_package(package: str) -> tuple[bool, str]:
    """Force-reinstall `package` into the interpreter currently running this
    process. Tries pip first -- it works whether the venv is stdlib-created or a
    `uv venv --seed` one. A uv venv built without --seed has no pip module at all
    (install.sh never assumes one either), so a "No module named pip" failure
    falls back to `uv pip install --python <this interpreter>`, the same command
    install.sh itself uses to populate the venv in the first place."""
    pip_result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--force-reinstall", package],
        capture_output=True, text=True, timeout=180,
    )
    if pip_result.returncode == 0:
        return True, f"reinstalled {package} via pip"

    uv = shutil.which("uv")
    if uv and "No module named pip" in (pip_result.stderr or ""):
        uv_result = subprocess.run(
            [uv, "pip", "install", "--python", sys.executable,
             "--force-reinstall", package],
            capture_output=True, text=True, timeout=180,
        )
        if uv_result.returncode == 0:
            return True, f"reinstalled {package} via uv"
        return False, (uv_result.stderr or uv_result.stdout or "unknown uv error")[-300:]

    return False, (pip_result.stderr or pip_result.stdout or "unknown pip error")[-300:]
```

**Step 2: Confirm `subprocess`, `sys`, `shutil` are already imported**

```bash
grep -n "^import subprocess\|^import sys\|^import shutil" src/agent8088/cli.py
```
Expected: all three present (this codebase already shells out elsewhere in `cli.py`).
If any is missing, add it to the top-level import block — do not import inside the
function.

**Step 3: Commit**

```bash
git add src/agent8088/cli.py
git commit -m "feat(cli): add a pip/uv package-reinstall helper for doctor --fix"
```

---

### Task C2: Wire `--fix` into `cmd_doctor`

**Files:** Modify: `src/agent8088/cli.py:2492`

**Step 1: Change the signature and add the repair pass**

```diff
-def cmd_doctor(_):
+def cmd_doctor(rest):
+    fix = rest.strip().lower() == "--fix"
     active = _active_provider_name()
```

**Step 2: Add the repair pass after the table prints (end of the existing function
body, right after `console.print(t)`)**

```python
    if fix:
        console.print("[dim]Checking for auto-repairable issues...[/dim]")
        if A.web_search._ddgs_installed():
            console.print("[dim]No auto-repairable issues found.[/dim]")
        else:
            ok, detail = _reinstall_package("ddgs")
            if ok and A.web_search._ddgs_installed():
                console.print(f"[green]Fixed:[/green] web search — {detail}")
            elif ok:
                console.print(
                    f"[yellow]Reinstalled ddgs but it still fails to import[/yellow] "
                    f"({detail}) — this usually means a missing system library; "
                    f"see: pip install ddgs -v"
                )
            else:
                console.print(f"[red]Could not fix web search:[/red] {detail}")
                console.print(
                    f"  Manual repair: {sys.executable} -m pip install --force-reinstall ddgs"
                )
```

Re-checking `_ddgs_installed()` after the reinstall (rather than trusting the
subprocess's exit code alone) matters because the exact bug this repairs is "pip
reports success, import still fails" — a broken native wheel can reinstall cleanly and
still not import if the underlying system library is what's actually missing.

**Step 3: Verify manually**

```bash
python3 -c "
import sys
sys.path.insert(0, 'src')
from agent8088 import cli
print(cli.cmd_doctor.__code__.co_varnames[:1])
"
```
Expected: `('rest',)`.

**Step 4: Commit**

```bash
git add src/agent8088/cli.py
git commit -m "feat(cli): add doctor --fix to auto-repair a broken ddgs install"
```

---

### Task C3: Update `/help` text and the doctor row for discoverability

**Files:** Modify: `src/agent8088/cli.py:1940`

**Step 1: Replace the help entry**

```diff
-        ("/doctor", "Check model endpoint reachability, auth/config, tools, and skills"),
+        ("/doctor [--fix]", "Check model endpoint reachability, auth/config, tools, and skills; --fix repairs a broken web-search install"),
```

**Step 2: Verify**

```bash
grep -n '"/doctor \[--fix\]"' src/agent8088/cli.py
```
Expected: one match.

**Step 3: Commit**

```bash
git add src/agent8088/cli.py
git commit -m "docs(cli): document doctor --fix in /help"
```

---

### Task C4: Test the repair path with a fake broken package

**Files:** Create: `tests/test_doctor_fix.py`

**Step 1: Write the test**

```python
"""doctor --fix's reinstall helper, exercised against a fake pip/uv rather than a
real broken package -- reinstalling a genuinely broken native wheel isn't something
a test should attempt to reproduce; this pins the subprocess/fallback logic instead.
"""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent8088 import cli  # noqa: E402


def test_reinstall_package_succeeds_via_pip(monkeypatch):
    def fake_run(cmd, **kwargs):
        assert cmd[:3] == [sys.executable, "-m", "pip"]
        return subprocess.CompletedProcess(cmd, 0, stdout="Successfully installed ddgs", stderr="")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    ok, detail = cli._reinstall_package("ddgs")
    assert ok is True
    assert "pip" in detail


def test_reinstall_package_falls_back_to_uv_when_pip_module_missing(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if "pip" in cmd and cmd[0] == sys.executable:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="No module named pip")
        return subprocess.CompletedProcess(cmd, 0, stdout="installed", stderr="")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/local/bin/uv" if name == "uv" else None)
    ok, detail = cli._reinstall_package("ddgs")
    assert ok is True
    assert "uv" in detail
    assert any(c[0] == "/usr/local/bin/uv" for c in calls)


def test_reinstall_package_reports_failure_when_both_fail(monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="permission denied")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    ok, detail = cli._reinstall_package("ddgs")
    assert ok is False
    assert "permission denied" in detail
```

**Step 2: Run it**

```bash
python3 -m pytest tests/test_doctor_fix.py -v
```
Expected: `3 passed`.

**Step 3: Commit**

```bash
git add tests/test_doctor_fix.py
git commit -m "test(cli): cover doctor --fix's pip/uv reinstall fallback"
```

---

# Part D — `agent8088 dump`: a shareable, redacted diagnostic bundle

Mirrors Hermes's three-way split (`doctor` repairs, `status` overviews, `dump` shares)
— Agent8088 has the first two; this adds the third, so a failure on someone else's
machine can be diagnosed from a pasted file instead of a live screen-share.

### Task D1: Implement `cmd_dump`

**Files:** Modify: `src/agent8088/cli.py` — insert after `cmd_doctor`

**Step 1: Write the function**

```python
def cmd_dump(_rest):
    """Write a redacted, shareable diagnostic bundle to APP_DIR/dump.txt."""
    import platform

    active = _active_provider_name()
    provider = A.PROVIDERS.get(active, {})
    sandbox = A.sandbox_status()

    lines = [
        f"Agent8088 diagnostic dump — {A.VERSION if hasattr(A, 'VERSION') else 'unknown version'}",
        f"Generated: this file was written by `agent8088 dump`; review before sharing.",
        "",
        "## System",
        f"OS: {platform.system()} {platform.release()} ({platform.machine()})",
        f"Python: {sys.version.split()[0]} at {sys.executable}",
        f"Shell: {os.environ.get('SHELL', 'unknown')}",
        "",
        "## Provider",
        f"Active: {active}",
        f"Model: {A.MODEL_NAME}",
        f"Endpoint reachable: {_endpoint_probe(provider.get('base_url') or A.MODEL_BASE_URL)}",
        "",
        "## Sandbox",
        f"Requested: {sandbox['requested']}",
        f"Resolved: {sandbox['resolved']} ({sandbox['verification']})",
        f"Detail: {sandbox['detail']}",
        "",
        "## Configuration",
        f"Config path: {A.CONFIG_PATH} (exists={A.CONFIG_PATH.exists()})",
        f"Tools: {len(_active_tool_specs())}  Skills: {len(_active_skills())}",
    ]

    text = "\n".join(lines) + "\n"
    # Defense in depth: this function never touches api keys/tokens by construction
    # (nothing above reads them), but scrub anyway using the same secret list every
    # other tool-output path redacts through, in case a future edit adds a field
    # that does.
    for secret in A.collect_secret_values(A.APP_CONFIG):
        text = text.replace(secret, "[REDACTED]")

    out_path = A.APP_DIR / "dump.txt"
    out_path.write_text(text, encoding="utf-8")
    console.print(f"Diagnostic bundle written to [#00edff]{out_path}[/#00edff]")
    console.print("[dim]Reviewed for secrets before sharing — no API keys or tokens are included.[/dim]")
```

**Step 2: Register it**

```diff
-    "status": cmd_status, "doctor": cmd_doctor, "sandbox": cmd_sandbox, "mode": cmd_mode,
+    "status": cmd_status, "doctor": cmd_doctor, "dump": cmd_dump, "sandbox": cmd_sandbox, "mode": cmd_mode,
```

(`src/agent8088/cli.py:3582`)

**Step 3: Add the help-table row**

```diff
         ("/doctor [--fix]", "Check model endpoint reachability, auth/config, tools, and skills; --fix repairs a broken web-search install"),
+        ("/dump", "Write a redacted diagnostic bundle to disk, for sharing in a bug report"),
```

**Step 4: Verify**

```bash
python3 -c "
import sys
sys.path.insert(0, 'src')
from agent8088 import cli
assert 'dump' in cli.COMMANDS
print('registered')
"
```
Expected: `registered`.

**Step 5: Commit**

```bash
git add src/agent8088/cli.py
git commit -m "feat(cli): add /dump for a redacted, shareable diagnostic bundle"
```

---

### Task D2: Test that dump never leaks a configured secret

**Files:** Create: `tests/test_dump_redaction.py`

**Step 1: Write the test**

```python
"""cmd_dump must never write a configured secret to disk, even if a future edit
adds a field that reads one. This pins that guarantee at the redaction layer rather
than by enumerating every field cmd_dump currently writes."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent8088 import cli  # noqa: E402


def test_dump_redacts_a_configured_secret(tmp_path, monkeypatch):
    monkeypatch.setattr(cli.A, "APP_DIR", tmp_path)
    monkeypatch.setattr(cli.A, "APP_CONFIG", {"provider.openai.api_key": "sk-super-secret-value"})
    monkeypatch.setattr(cli.A, "collect_secret_values", lambda config: ["sk-super-secret-value"])

    cli.cmd_dump("")

    dump_text = (tmp_path / "dump.txt").read_text(encoding="utf-8")
    assert "sk-super-secret-value" not in dump_text
```

**Step 2: Run it**

```bash
python3 -m pytest tests/test_dump_redaction.py -v
```
Expected: `1 passed`. If `cli.cmd_dump` reads any attribute not stubbed here (e.g. a
live provider connection), the failure will name the missing attribute — stub it the
same way as `APP_DIR`/`APP_CONFIG` above rather than letting the test touch the
network.

**Step 3: Commit**

```bash
git add tests/test_dump_redaction.py
git commit -m "test(cli): pin that /dump redacts configured secrets"
```

---

# Part E — Publish the compatibility matrix

### Task E1: Add a trimmed platform-support table to README

**Files:** Modify: `README.md` — insert after the Quick Start install commands (after
the block edited in Task A1)

**Step 1: Add the table**

```markdown
### Supported platforms

| Platform | Status | Notes |
|---|---|---|
| macOS 12+ (Apple Silicon & Intel) | Supported | `install.sh` |
| Ubuntu / Debian / Fedora / Arch (x64, arm64) | Supported | `install.sh` |
| WSL2 | Supported | `install.sh`; clone with LF line endings, not CRLF |
| Windows 10 (1903+) / 11, in Windows Terminal | Supported | `install.ps1` |
| Windows Server, legacy Console Host, PowerShell ISE | Not supported | needs a modern terminal host — see `install.ps1`'s terminal check |
| Alpine / other non-glibc Linux | Best-effort | works if bash, curl-or-wget, and Python 3.10+ are present |
| Corporate proxy (`HTTP_PROXY`/`HTTPS_PROXY`) | Supported | both installers honor standard proxy env vars |

Run `agent8088 doctor` after installing to verify your setup, or `agent8088 dump` to
produce a bundle for a bug report.
```

**Step 2: Verify**

```bash
grep -n "Supported platforms" README.md
```
Expected: one match.

**Step 3: Commit**

```bash
git add README.md
git commit -m "docs: publish a supported-platforms table in the README"
```

---

# Part F — Final verification

### Task F1: Run the full test suite

```bash
python3 -m pytest tests/ -k "installer or doctor or dump" -v
bash scripts/check_installer_portability.sh install.sh
bash -n install.sh
```
Expected: all new and existing installer/doctor/dump tests pass; portability lint
prints `portability OK: install.sh`; syntax check is silent.

### Task F2: Manual smoke test of `doctor --fix` and `dump`

```bash
agent8088 --version   # confirm the CLI is on PATH from a normal install
agent8088 doctor --fix
agent8088 dump && cat "$(python3 -c 'from agent8088 import engine as A; print(A.APP_DIR)')/dump.txt"
```
Expected: `doctor --fix` reports either "No auto-repairable issues found" or a fix
result; `dump.txt` contains no string matching any configured API key.

### Task F3: Confirm the README's edited one-liners still work end-to-end

Not automatable safely (it invokes the real installer against a real GitHub raw URL);
note this as a manual pre-release check rather than a task, run once per platform
family before merging Part A/E to `development`:

```bash
curl -fsSL --proto '=https' --tlsv1.2 https://raw.githubusercontent.com/RT-Internal-DS/Agent8088-Features-added/development/install.sh | bash
```

---

## Open questions

1. **Should `doctor --fix` grow beyond ddgs?** The only other "detected but not
   repaired" state found in this research pass is ddgs. If a second one surfaces later
   (e.g. a broken Playwright browser install), the same `_reinstall_package` helper
   generalizes directly — add a check + call, no new infrastructure needed.
2. **Should `dump` redact file paths (usernames in `$HOME`)?** Left in for now since
   they're useful for debugging path-length/permission issues (the exact class of
   scratch-PC failure this whole effort targets) and are not secrets — flag if this
   should change.
