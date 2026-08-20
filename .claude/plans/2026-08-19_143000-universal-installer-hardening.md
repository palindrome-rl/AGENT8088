# Installer Hardening: Timeouts, Always-On Setup, Hermes-Aligned ddgs

> **For the implementer:** work task-by-task, in order. Each task is 2–5 minutes.
> Commit after every task. Do not batch.

**Repo:** `/Users/tahawaheed/Documents/Agent8088-Features-added`
**Branch:** `development` — pulled to `d296ab5` *(“feat: require modern Windows terminal”)*.
`development` is **68 commits ahead of `main`**, so `main` is stale. Work here, branch off here.

**Goal:** Make `install.sh` / `install.ps1` incapable of hanging, make first-run setup
appear on every run, make the keyless `ddgs` fallback reliable using Hermes' documented
practice, and make all of it work across the shells, hosts, architectures and OSes in
the compatibility contract — or fail with one specific, actionable line.

**Architecture:** Six parts in dependency order.
`A` builds the timeout + skipped-stage infrastructure (ported, not hand-written) →
`B` hardens it and applies it everywhere → `C` adds portability guards →
`D` rewrites the ddgs provider → `E` un-gates first-run setup → `F` verifies.

**Tech stack:** bash 3.2+ (no bash-4 constructs), Windows PowerShell 5.1 **and**
PowerShell 7.x, Python 3.10+, `ddgs>=9,<10`, pytest.

---

## Revision notes — what changed and why

**1. Branch retarget (again).** Earlier revisions were written against
`PalindromeRL/AGENT8088` and then against `codex/search-public-fallback`. Both sets of
line numbers are void. Every line number below was re-read from `development@d296ab5`
after `git pull`.

**2. `main` is not the branch.** `origin/development` is 68 commits ahead of
`origin/main`. Anything landed against `main` would be immediately stale.

**3. The Windows-terminal gate is OUT OF SCOPE — by your decision.**
`d296ab5` added `Ensure-SupportedTerminal` (`install.ps1:203-283`) as the **first**
call in `# Main` (`:1234-1236`). It `exit 1`s unless the host is VS Code's terminal or
Windows Terminal ≥ 1.19.0.0, offering a winget install first.

You said **“skip this fix then”**, so no task below modifies it. State the consequence
plainly rather than letting the plan imply otherwise: **that gate, not this plan, is
what decides which Windows hosts can install.** It currently refuses legacy conhost,
Windows Server (no Windows Terminal package), Windows 10 pre-1903 (Windows Terminal
needs 1903+), machines without winget/App Installer, and any `$NonInteractive` run
(*“Interactive confirmation is required…”* → `exit 1`). So “universal on Windows” means
universal **within modern-terminal hosts**. The original brief asked for “windows
legacy powershell”; that is not achievable while the gate stands, and the plan no
longer claims it. Reopen it whenever you want — Open Question 1.

**Two consequences that are still in scope, because the gate depends on them:**
- **TLS 1.2 must be set *before* the gate runs.** `Start-InstallerInWindowsTerminal`
  (`:230`) re-downloads the installer with `Invoke-RestMethod` from
  `raw.githubusercontent.com` when `$PSCommandPath` is empty — i.e. on the documented
  `iex (irm …)` path. On a PS 5.1 host defaulting to TLS 1.0 that relaunch fails with
  a TLS error. → Task C4, ordered before the gate.
- **A PowerShell version floor must run *before* the gate.** The gate uses
  `Get-AppxPackage` and `-notin`, so on PS 3.0/4.0 it fails from inside with a cryptic
  message instead of a clear “5.1+ required”. → Task C5.

**4. Adopt the repo's existing installer-test convention.** `tests/` exists on this
branch with `test_installer_terminal.py` and `test_installer_initial_setup.py`. Both
use a genuinely good trick: regex-extract **one** PowerShell function out of
`install.ps1` and run it in isolation via `pwsh`/`powershell`:

```python
def _powershell_function(name: str) -> str:
    source = (ROOT / "install.ps1").read_text(encoding="utf-8")
    match = re.search(rf"(?ms)^function {re.escape(name)} \{{.*?^\}}$", source)
    assert match, f"PowerShell function not found: {name}"
    return match.group(0)
```

This replaces the bespoke `tests/install/Test-Timeouts.ps1` harness the previous
revision invented, and it removes the need for an `AGENT8088_INSTALL_NO_RUN` guard in
`install.ps1`. **Two constraints follow:** every new PowerShell function must be
formatted `^function Name {` … `^}$` (no `function Name{`, no trailing brace indent)
or the regex will not find it; and helpers must not depend on script-scope state the
extracted function does not carry.

**5. An existing test pins the behaviour Part E changes.**
`tests/test_installer_initial_setup.py` asserts
`(fresh=False, config=False) → "Existing installation and config found"` — exactly the
gate Part E removes. **Part E must update that test in the same commit**, or the suite
goes red. Task E1 Step 4.

**6. PowerShell installed locally — verified.** `powershell` is now a Homebrew
**formula** (`brew install powershell`), not a cask: `--cask powershell` was removed
upstream and only `powershell@preview` remains there. Installed 7.6.5 (Core) at
`/opt/homebrew/bin/pwsh`, no sudo needed.

Measured effect on this branch's installer tests:

```
before:  1 passed, 9 skipped   ("Skipped: PowerShell is not installed")
after:  15 passed in 4.26s
```

Caveat that shapes Task F1: this is **pwsh 7**, so it exercises the PowerShell-7 branch
of every `if`. The 5.1-only paths — manual argv quoting, `HttpWebRequest.Timeout`
semantics, TLS defaults — still need real Windows, and F1 marks those assertions
`skipif` rather than letting them pass vacuously.

---

## Plain-English summary

Four things are wrong, and here they are without the jargon.

**1. The installer can freeze forever.** Nothing in either script has a time limit.
If a download stalls halfway — the server answers, then just stops sending — the
installer sits there looking busy until you press Ctrl-C. The worst spot is the step
that installs the main Python packages, because that one downloads big files.
*Fix:* put a stopwatch on every step. When it runs out, kill the step, say which one
died and how to fix it, and move on. Also tell each tool (`uv`, `pip`, `git`, `curl`)
to give up on its own if a connection goes quiet — so a dead connection fails in a
minute instead of eating the whole stopwatch.

**2. The setup questions only appear the first time.** The dialog that asks for your
working folder, model, and web search is skipped on any re-run. That is backwards: if
something failed the first time, you re-run it, and that is exactly when you want to
be asked again. *Fix:* always ask. Only two things still skip it — you passing
`--skip-setup`, or there being no keyboard attached at all.

**3. Web search is unreliable.** Three separate problems:
- The check for "is the search library installed?" only looks for a *folder* on disk.
  If the folder is there but the library is broken, the check says "yes, all good" and
  then every search crashes. That is why it works on some machines and not others.
- The library can search many engines, but we never tell it which, so it defaults to
  Wikipedia-style sources — useless for "what's the latest price/release/news".
- One search, one try. DuckDuckGo throttles you, and the search just fails. The agent
  then repeats similar searches seconds apart, which makes the throttling worse.
*Fix:* actually try importing the library; name several engines so a blocked one rolls
over to the next; wait a moment between searches; retry a throttled search; and
remember a repeated question for 5 minutes so it costs nothing.

**4. It breaks on some systems.** Pasting `curl … | sh` instead of `| bash` fails with
a confusing error 200 lines later. Corporate networks with a proxy fail completely —
there is no proxy support at all. On Windows, deeply-nested `node_modules` blows past
the 260-character path limit. *Fix:* detect each case up front and either handle it or
print one line saying exactly what to do.

**On Windows specifically:** someone added a rule this morning that refuses to install
unless you are in the modern Windows Terminal. You asked me to leave that alone, so I
have. It does mean the old-style console, Windows Server, and older Windows 10 are
blocked before my changes ever run — that rule decides it, not this plan.

---

## The compatibility contract

“Universal” needs a boundary, or it is untestable. Each row is **supported** (works),
**guarded** (refuses with one actionable line), or **blocked upstream** (refused by the
terminal gate, which is out of scope).

### Shells / hosts

| Target | Status | Current behaviour | Task |
|---|---|---|---|
| bash 3.2 (stock macOS) | supported | works — no `declare -A`, `${x,,}`, `mapfile`, `\|&` anywhere | C2 pins it |
| bash 4.x / 5.x | supported | works | — |
| `sh` / dash / busybox ash | **guarded** | **breaks silently** — `curl … \| sh` hits bash-array syntax, error surfaces far later | C1 |
| zsh | **guarded** | arrays are 1-indexed; splitting differs | C1 |
| PowerShell 7.x in Windows Terminal | supported | — | — |
| Windows PowerShell 5.1 in Windows Terminal | supported after C4 | needs TLS 1.2 + the 5.1 quoting path | A2, C4 |
| PowerShell in VS Code terminal | supported | gate allows via `$env:TERM_PROGRAM` | — |
| **Legacy Windows Console Host (conhost)** | **blocked upstream** | `Ensure-SupportedTerminal` → offer winget, else `exit 1` | out of scope |
| **PowerShell ISE** | **blocked upstream** | no `WT_SESSION` → refused by the gate | out of scope |
| Windows PowerShell 3.0 / 4.0 | **guarded** | gate's `Get-AppxPackage` fails cryptically from inside | C5 |

### OS / architecture

| Target | Status | Task |
|---|---|---|
| Windows 10 ≥1903 + Win11, x64/ARM64, with Windows Terminal | supported | F2 |
| Windows 10 <1903, Server 2016–2025 | **blocked upstream** (no Windows Terminal) | out of scope |
| macOS 12–15, Intel + Apple Silicon | supported — no `timeout(1)` on macOS, so the fallback watchdog matters | A1, F1 |
| Ubuntu / Debian / Fedora / Arch, x64 + arm64 | supported | F3 |
| Alpine / busybox (no bash) | **guarded** | C1 |
| WSL1 / WSL2 | supported — CRLF risk if cloned on Windows | C3 |
| FreeBSD / other Unix | **guarded** — `detect_os` currently warns then **continues**, failing later | C1 |
| Termux / Android | **guarded** — see Open Question 4 | C1 |

### Environments

| Condition | Status | Task |
|---|---|---|
| No admin rights | supported (user-scoped throughout) | — |
| `ExecutionPolicy Restricted` | supported via `iex (irm …)` | F2/T9 |
| **Corporate proxy** | **broken — zero proxy handling in either file** | C6 |
| Spaces in paths | supported (`ConvertTo-LongPath` 8.3 normalisation) | F2/T7 |
| Non-ASCII paths | **untested** | F2/T11 |
| **Windows MAX_PATH (260)** | **broken** — bridge `node_modules` nesting overruns it | C7 |
| Offline / airgapped | **guarded** — must fail fast, not hang | B-series |
| CRLF checkout of `install.sh` | **guarded** | C3 |
| Non-interactive / CI on Windows | **blocked upstream** — gate requires interactive confirmation | out of scope |

---

## Current context — re-verified on `development@d296ab5`

| Fact | Evidence |
|---|---|
| `install.sh` 1275 lines; `install.ps1` 1250 lines | `wc -l` |
| **No timeout machinery at all** | `grep -c run_with_timeout install.sh` → `0`; `grep -c Invoke-WithTimeout install.ps1` → `0` |
| **No skipped-stage ledger** | `record_skip` → `0`; `Register-SkippedStage` → `0` |
| **No TLS setting** | `grep -c SecurityProtocol install.ps1` → `0` |
| **No proxy handling** | `grep -c proxy install.sh install.ps1` → `0`, `0` |
| Every `Invoke-WebRequest -OutFile` is bare — not even `-TimeoutSec` | `ps1:481`, `:582`, `:766` |
| `Start-Process -Wait` unbounded (PortableGit extractor) | `ps1:491` |
| Core editable install unguarded — **the stage that hangs** | `ps1:636`, `sh:432-434` |
| `uv venv` unguarded | `ps1:622`, `:628` |
| uv bootstrap unguarded | `ps1:336`, `sh:187` |
| `git clone` / `fetch` unguarded | `sh:376`, `:383` |
| Node tarball `curl` unguarded | `sh:604` |
| Optional stages unguarded | `ps1:670,680,691,694`, `sh:448-492` |
| Setup gated on fresh-or-new-config | `sh:1217`, `ps1:1193` |
| Terminal gate is first in `# Main` | `ps1:1234-1236`, function at `:203-283` |
| `tests/` exists, with 2 installer test modules | `tests/test_installer_terminal.py`, `tests/test_installer_initial_setup.py` |
| bash 3.2-safe today | portability grep finds no bash-4 construct |
| `detect_os` continues on unknown OS | `sh:131` + its `*)` arm |
| `. /etc/os-release` leaks vars into the installer's shell | `sh` `detect_os` |
| ddgs is a **core** dep; `[search]` is a back-compat alias | `pyproject.toml` |
| **`web_search.py` unchanged — 598 lines, all four bugs intact** | `:411`, `:415`, `:419`, `:476` |

---

## Hermes reference — what it actually prescribes

From [the web-search feature docs](https://hermes-agent.nousresearch.com/docs/user-guide/features/web-search),
[the DuckDuckGo skill page](https://hermes-agent.nousresearch.com/docs/user-guide/skills/optional/research/research-duckduckgo-search),
and [the provider-plugin guide](https://hermes-agent.nousresearch.com/docs/developer-guide/web-search-provider-plugin).
`agent/web_search_provider.py` and `agent/web_search_registry.py` could **not** be read
— GitHub returned **HTTP 429** to both `gh api` and `raw.githubusercontent.com`.
**Retry them in Task D1** and reconcile.

Be honest about what this settles: Hermes' documented ddgs configuration is *minimal* —
a bare `DDGS()`, no `backend=`, no tuned timeout, and rate-limit guidance in prose
rather than code. The engine rotation in Task D6 therefore **exceeds** Hermes rather
than copying it. Four things Hermes does specify, all adopted:

1. **Context manager** — `with DDGS() as ddgs:`. Verified against ddgs 9.x:
   `__enter__`/`__exit__` exist and close the HTTP client and its connections. Current
   code builds a bare `DDGS()` per search and never closes it, leaking a client a call.
2. **`max_results` must always be a keyword argument** — stated as a hard constraint.
   Current code complies; the rewrite keeps it and a test pins it.
3. **A delay *between* searches, not only retries after failure** — *“DuckDuckGo may
   throttle after many rapid requests. Add a short delay between searches”* and *“If
   ddgs returns nothing, it may be rate-limited. Wait a few seconds and retry.”* This
   is what the previous revision missed: a repeat-query cache helps identical queries,
   but an agent issuing three *different* searches in a loop still burns the window.
   → `_DDGS_MIN_INTERVAL`.
4. **Cloud IPs get blocked** — *“DuckDuckGo may block requests from some cloud IPs.”*
   ddgs accepts `proxy=` / `DDGS_PROXY`; a VPS install has no recourse today.
   → optional `search_proxy` key.

Hermes also orders its chain `tavily → exa → parallel → firecrawl → searxng →
brave-free → ddgs`, putting ddgs **last**. This repo has
`PREFERENCE = ("searxng", "ddgs", "tavily", "exa")` (`web_search.py:51`). Aligning is a
one-line change with a behaviour consequence, so it is **not** in any task — Open
Question 3.

---

## Root cause analysis

### Issue 1 — “the timeout does not work; a stuck package stays stuck forever”

On this branch the answer is simple: **there is no timeout anywhere.** `grep -i timeout`
over both installers returns nothing. All ~30 network calls listed above can hang
forever, and the core editable install (`ps1:636`, `sh:432`) — which pulls playwright's
and ddgs's native wheels — is the most likely to stall, matching the report.

- **1a.** No wall-clock wrapper on any stage. → A1, A2, B5, B6
- **1b.** `Invoke-WebRequest -OutFile` cannot be bounded *even with* `-TimeoutSec`. On
  5.1 that maps to `HttpWebRequest.Timeout`, covering only up to the response headers
  (the body has a separate per-read `ReadWriteTimeout`); on 7.x it maps to
  `HttpClient.Timeout`, which stops applying once headers arrive and the copy to
  `-OutFile` begins. A server that accepts, sends headers, then dribbles bytes hangs on
  both. → B2, B3
- **1c.** SIGTERM alone loses to a child that traps it (npm's node wrapper). Needs `-k`,
  with a probe because older busybox `timeout` lacks it. → B1
- **1d.** No tool-level stall detection, so a dead socket burns the whole budget inside
  one request instead of failing at 60s and retrying a working mirror. → B4

### Issue 2 — “first-run setup must come up every single time”

`sh:1217` `[ "$FRESH_INSTALL" != true ] && [ "$CONFIG_CREATED" != true ]` and
`ps1:1193`. `bec7703` widened this with the `CONFIG_CREATED` escape hatch, but a re-run
over an existing install *with* an existing config still skips — which is precisely the
run where it matters, because a run where an optional stage failed still installs a
working agent and leaves the user with no prompt and no hint that `agent8088 --setup`
exists.

Secondary: a missing shim/`.exe` makes the function warn and return rather than falling
back to `python -m agent8088.cli --setup`; and the dead wizards (`sh` `run_setup_wizard`,
`ps1` `Run-SetupWizard`) duplicate the prompts, which is why the gate reads as more
conditional than it is.

### Issue 3 — “ddgs detected sometimes, and rate-limits on the second search”

`web_search.py` is untouched on this branch, so all of these stand:

- **3a.** `_ddgs_installed()` is `find_spec("ddgs") is not None` (`:415`) — it answers
  “is there a module of this name on `sys.path`”, which stays `True` for a distribution
  whose Python files landed but whose native dependency did not. `/search doctor`
  (`cli.py:2606`) then prints **“ddgs importable: yes”** while every search dies on
  ImportError. That is the hit-and-miss detection.
- **3b.** `_ddgs_text` (`:419`) omits `backend=`, taking upstream's `auto`, documented as
  prioritising **Wikipedia and Grokipedia** — sources answering almost none of what
  `web_search` is for (`tools.txt`: “current leaders, releases, prices, availability,
  schedules, news”).
- **3c.** One attempt, no retry, no inter-search delay, no cache.
- **3d.** `"202" in message` (`:476`) matches any year or byte count, so unrelated
  failures are reported as throttling, with throttling advice.
- **3e.** The egress check refuses the whole backend if **any** of 3 hosts is blocked
  (`:466`). Widening the engine list under that rule would make ddgs *more* likely to
  be denied — 10 hosts to allow instead of 3. Filter per-engine instead.
- **3f.** A bare `DDGS()` per search leaks an HTTP client (Hermes uses a context manager).

---

# Part A — Build the infrastructure (port, don't hand-write)

`PalindromeRL/AGENT8088@0bdd93c` contains a reviewed implementation. Copying it
preserves fixes that are not obvious from the signature.

`REF=/Users/tahawaheed/Documents/PalindromeRL/AGENT8088/.claude/worktrees/install-script-fixes-testing-b61b2c`

### Task A1: Port `run_with_timeout` + the skipped-stage ledger into `install.sh`

**Files:** Modify: `install.sh` — insert after the `log_*` definitions (~`:91`)

**Step 1: Extract**

```bash
REF=/Users/tahawaheed/Documents/PalindromeRL/AGENT8088/.claude/worktrees/install-script-fixes-testing-b61b2c
sed -n '93,215p' "$REF/install.sh" > /tmp/timeout-block.sh && wc -l /tmp/timeout-block.sh
```
Expected ~123 lines: the `TIMEOUT_SCALE` table, `run_with_timeout`, `SKIPPED_STAGES`,
`record_skip`, `warn_stage`, `print_skipped_summary`.

**Step 2: Insert, keeping every comment.** They document why the fallback watchdog
escalates TERM→KILL and why 137/143 normalise to 124.

**Step 3: Call `print_skipped_summary` as the last line of `verify_install`** — so a
failed stage is restated after the multi-minute scroll, not only when it happened.

**Step 4: Verify**

```bash
cd /Users/tahawaheed/Documents/Agent8088-Features-added
bash -n install.sh && echo "syntax OK"
grep -c "run_with_timeout\|record_skip\|warn_stage\|print_skipped_summary" install.sh
```
Expected: `syntax OK`, count ≥ 8.

**Step 5: Commit**

```bash
git add install.sh && git commit -m "feat(install): add run_with_timeout and the skipped-stage ledger"
```

---

### Task A2: Port `Invoke-WithTimeout` + `Register-SkippedStage` into `install.ps1`

**Files:** Modify: `install.ps1` — insert after `Write-Err` (~`:114`), i.e. **before**
`Ensure-SupportedTerminal` so the gate can use the ledger later if wanted.

**Step 1: Extract**

```bash
sed -n '116,300p' "$REF/install.ps1" > /tmp/timeout-block.ps1 && wc -l /tmp/timeout-block.ps1
```
Expected ~185 lines: `$TimeoutScale`, the `$T*` table, `Invoke-WithTimeout`,
`$SkippedStages`, `Register-SkippedStage`, `Write-StageWarning`, `Write-SkippedSummary`.

**Step 2: Insert verbatim.** Three comments must survive — each records a bug invisible
from the code:
- built on `System.Diagnostics.Process`, not `Start-Process -PassThru`, because the
  latter does not reliably surface `.ExitCode` or honour `WaitForExit(ms)` on a
  redirected child — a “timeout” that never fired plus an exit code that always read 0;
- both pipes drained via `ReadToEndAsync` because a child that fills its stdout buffer
  while nobody reads blocks forever, reintroducing the exact hang;
- manual argv quoting for Windows PowerShell 5.1, whose .NET Framework
  `ProcessStartInfo` has no `ArgumentList`, because install paths routinely contain
  spaces (`C:\Users\First Last\…`).

**Step 3:** Confirm the brace style matches `^function Name {` … `^}$` so
`_powershell_function()` can extract it. Reformat if the port differs.

**Step 4: Call `Write-SkippedSummary` as the last line of `Verify-Install`.**

**Step 5: Verify with the repo's own convention**

```bash
cd /Users/tahawaheed/Documents/Agent8088-Features-added
python3 - <<'PY'
import re, pathlib
src = pathlib.Path("install.ps1").read_text(encoding="utf-8")
for fn in ("Invoke-WithTimeout", "Register-SkippedStage", "Write-StageWarning"):
    assert re.search(rf"(?ms)^function {re.escape(fn)} \{{.*?^\}}$", src), fn
print("all functions extractable")
PY
```
Expected: `all functions extractable`

**Step 6: Commit**

```bash
git add install.ps1 && git commit -m "feat(install): add Invoke-WithTimeout and the skipped-stage ledger"
```

---

# Part B — Harden, then apply everywhere

### Task B1: Make bash `timeout` escalate TERM → KILL

**Files:** Modify: `install.sh` — the `run_with_timeout` just added

**Step 1: Insert the probe immediately above it**

```bash
# Does this `timeout` understand -k (kill-after)? GNU coreutils >= 7 and busybox
# >= 1.30 do; older busybox treats -k as the command name and would run the wrong
# thing entirely. Probed once, because getting it wrong is silent.
_TIMEOUT_HAS_K=""
_timeout_supports_k() {
    if [ -z "$_TIMEOUT_HAS_K" ]; then
        if timeout -k 1 1 true >/dev/null 2>&1; then _TIMEOUT_HAS_K=yes
        else _TIMEOUT_HAS_K=no
        fi
    fi
    [ "$_TIMEOUT_HAS_K" = yes ]
}
```

**Step 2: Replace both `timeout` branches**

```bash
    # -k 10: escalate to KILL ten seconds after TERM. Without it a child that traps
    # or ignores TERM survives its own timeout and the installer hangs anyway --
    # the reported "the timeout does not work" symptom.
    if command -v timeout >/dev/null 2>&1; then
        if _timeout_supports_k; then
            timeout -k 10 "$_secs" "$@" || _rc=$?
        else
            timeout "$_secs" "$@" || _rc=$?
        fi
        case "$_rc" in 137|143) _rc=124 ;; esac
        return $_rc
    fi
    if command -v gtimeout >/dev/null 2>&1; then
        gtimeout -k 10 "$_secs" "$@" || _rc=$?
        case "$_rc" in 137|143) _rc=124 ;; esac
        return $_rc
    fi
```

The added `case` matters in both: with `-k`, a SIGKILLed child surfaces as 137, not
124, and `warn_stage` only recognises 124 as a hang.

**Step 3: Prove it on a TERM-ignoring child**

```bash
cd /Users/tahawaheed/Documents/Agent8088-Features-added
bash -c 'set -e
eval "$(sed -n "/^_TIMEOUT_HAS_K=/,/^}$/p;/^run_with_timeout()/,/^}$/p" install.sh)"
run_with_timeout 2 bash -c "trap \"\" TERM; sleep 60"; echo "rc=$?"'
```
Expected: returns in ~12s with `rc=124`. Without `-k`: `rc=143` at 2s, `sleep 60` still
running. (Extracting the two functions with `sed` avoids sourcing the whole installer.)

**Step 4: Commit**

```bash
git add install.sh && git commit -m "fix(install): escalate TERM to KILL in run_with_timeout"
```

---

### Task B2: Add `Invoke-BoundedDownload` to `install.ps1`

**Files:** Modify: `install.ps1` — after `Invoke-WithTimeout`

```powershell
# Download one file under a hard wall-clock limit covering the BODY transfer.
#
# Invoke-WebRequest cannot do this on either edition, with or without -TimeoutSec. On
# Windows PowerShell 5.1 that parameter maps to HttpWebRequest.Timeout, which covers
# only up to the response headers -- the body is governed by a separate per-read
# ReadWriteTimeout. On PowerShell 7 it maps to HttpClient.Timeout, which stops applying
# once headers arrive and the stream copy to -OutFile begins. Either way a server that
# accepts, sends headers, then dribbles bytes forever hangs the installer with no way
# out but Ctrl-C.
#
# WebClient.DownloadFileTaskAsync + Task.Wait(ms) bounds the whole operation and is the
# only API with one code path on 5.1 and 7.x. Obsolete in .NET Core, not removed.
# Returns a hashtable shaped like Invoke-WithTimeout's so callers branch the same way:
#   @{ Success = <bool>; TimedOut = <bool>; Error = <string> }
function Invoke-BoundedDownload {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter(Mandatory = $true)][string]$OutFile,
        [Parameter(Mandatory = $true)][int]$TimeoutSec,
        [System.Net.IWebProxy]$Proxy
    )

    $result = @{ Success = $false; TimedOut = $false; Error = "" }
    $client = $null
    try {
        $client = New-Object System.Net.WebClient
        # GitHub release assets 403 an absent User-Agent.
        $client.Headers.Add('User-Agent', 'agent8088-installer')
        # Passed in rather than read from script scope so the function stays testable in
        # isolation by tests/test_installer_timeouts.py.
        if ($Proxy) { $client.Proxy = $Proxy }

        $task = $client.DownloadFileTaskAsync($Uri, $OutFile)
        if ($task.Wait($TimeoutSec * 1000)) {
            if ($task.IsFaulted) {
                $result.Error = $task.Exception.GetBaseException().Message
            } else {
                $result.Success = $true
            }
        } else {
            $result.TimedOut = $true
            try { $client.CancelAsync() } catch { }
        }
    } catch {
        $result.Error = $_.Exception.Message
    } finally {
        if ($client) { try { $client.Dispose() } catch { } }
    }

    # A cancelled or faulted transfer leaves a truncated file. Removing it matters:
    # every caller's next step is Expand-Archive or a self-extractor, and a partial
    # archive fails there with a corruption error that names the wrong cause.
    if (-not $result.Success) {
        Remove-Item -Force -ErrorAction SilentlyContinue $OutFile
    }
    return $result
}
```

`-Proxy` is a **parameter**, not `$script:ResolvedProxy`, specifically so
`_powershell_function("Invoke-BoundedDownload")` can run it standalone.

**Verify:** Task F1. **Commit:**
`git add install.ps1 && git commit -m "feat(install): add Invoke-BoundedDownload with body-transfer timeout"`

---

### Task B3: Route the three bare downloads and the unbounded extractor through it

**Files:** Modify: `install.ps1:481` (PortableGit), `:491` (extractor), `:582` (repo
ZIP), `:766` (Node)

**Step 1: PortableGit (`:481`)** — mandatory, so throw:

```powershell
        $dl = Invoke-BoundedDownload -Uri $downloadUrl -OutFile $tmpFile `
                -TimeoutSec $TDownload -Proxy $script:ResolvedProxy
        if (-not $dl.Success) {
            $why = if ($dl.TimedOut) { "timed out after $([int]($TDownload / 60))m" } else { $dl.Error }
            throw "Downloading $assetName failed: $why"
        }
```

**Step 2: The self-extractor (`:491`)** — `Start-Process -Wait` is unbounded; a
self-extractor stuck on an AV-locked file hangs the install:

```powershell
            Write-Info "Extracting PortableGit to $gitDir ..."
            $extract = Invoke-WithTimeout -FilePath $tmpFile `
                -Arguments @("-o$gitDir", "-y") -TimeoutSec $TExtract
            if ($extract.TimedOut) { throw "PortableGit extraction timed out after $([int]($TExtract / 60))m" }
            if ($extract.ExitCode -ne 0) { throw "PortableGit extraction failed (exit $($extract.ExitCode))" }
```

Note the argument change: `Invoke-WithTimeout` quotes each element itself, so a
pre-quoted `"-o`"$gitDir`""` must become plain `"-o$gitDir"` or it double-quotes on 5.1.

**Step 3: Repo ZIP (`:582`)** — mandatory, throw.
**Step 4: Node (`:766`)** — optional, so warn and register a skip:

```powershell
            $dl = Invoke-BoundedDownload -Uri $downloadUrl -OutFile $tmpFile `
                    -TimeoutSec $TDownload -Proxy $script:ResolvedProxy
            if (-not $dl.Success) {
                Write-StageWarning -Result @{ ExitCode = -1; TimedOut = $dl.TimedOut } `
                    -TimeoutSec $TDownload -What "Node.js download" `
                    -Consequence "WhatsApp bridge unavailable" -Fix "rerun the installer"
                return
            }
```

**Step 5: Verify no unbounded transfer remains**

```bash
grep -n "Invoke-WebRequest\|Start-Process" install.ps1
```
Expected: the comment at `:26`, the `irm` inside `Install-Uv`'s child process, and
`Start-Process` at `:251` inside the out-of-scope terminal gate. Nothing else.

**Commit:** `git add install.ps1 && git commit -m "fix(install): bound every download and the PortableGit extractor"`

---

### Task B4: Tool-level stall guards, both files

**Step 1: `install.sh`, after `export UV_NO_CONFIG=1`**

```bash
# ---------------------------------------------------------------------------
# Tool-level stall guards
# ---------------------------------------------------------------------------
# The wall-clock wrappers are a backstop, not the first line of defence. Without
# these, a registry that accepts the connection and then goes quiet burns the ENTIRE
# budget inside one dead request, so the wrapper's kill is the first thing that
# happens rather than a fast failure and a retry against a working mirror.
# Only set when unset, so an operator on a genuinely slow link can raise them.
export UV_HTTP_TIMEOUT="${UV_HTTP_TIMEOUT:-60}"
export PIP_DEFAULT_TIMEOUT="${PIP_DEFAULT_TIMEOUT:-60}"
export PIP_RETRIES="${PIP_RETRIES:-3}"

# git aborts a transfer that stays under 1 KB/s for 60s. This is what bounds
# `git clone` / `git fetch` -- wrapping them would swallow their progress output.
export GIT_HTTP_LOW_SPEED_LIMIT="${GIT_HTTP_LOW_SPEED_LIMIT:-1000}"
export GIT_HTTP_LOW_SPEED_TIME="${GIT_HTTP_LOW_SPEED_TIME:-60}"

# Curl flags for every download: fail on a dead handshake, and abort a transfer
# crawling under 1 KB/s for 30s. A space-separated string, spliced unquoted, so this
# block stays usable before bash is confirmed (see the C1 preflight).
CURL_STALL_FLAGS="--connect-timeout 20 --speed-limit 1024 --speed-time 30"
```

**Step 2: Apply at `sh:187` (uv installer) and `sh:604` (Node tarball)**

```bash
    if ! curl -LsSf $CURL_STALL_FLAGS --max-time 120 \
            https://astral.sh/uv/install.sh -o "$_uv_installer" 2>/dev/null; then
```

```bash
            if run_with_timeout "$T_NODE_DL" curl -fsSL $CURL_STALL_FLAGS "$_url" -o "$_tmp" 2>/dev/null; then
```

No `--max-time` on the second: `run_with_timeout` supplies the wall clock, and two
competing caps make the effective limit ambiguous.

**Step 3: `install.ps1`, after `$env:UV_NO_CONFIG = "1"`** — the same four env vars via
`if (-not $env:X) { $env:X = "…" }`.

**Step 4: Verify**

```bash
grep -c "GIT_HTTP_LOW_SPEED_LIMIT" install.sh install.ps1
grep -c "CURL_STALL_FLAGS" install.sh   # expect 3
```

**Commit:** `git add install.sh install.ps1 && git commit -m "fix(install): configure uv/pip/git/curl stall detection"`

---

### Task B5: Guard the mandatory stages (bash)

A timeout on a mandatory stage is a **hard failure with a specific message**, not a skip.

**Files:** Modify: `install.sh` — the `$T_*` table, plus `:187`, `:376`, `:383`,
`:400-404`, `:432-434`

**Step 1: Add budgets**

```bash
T_CORE_INSTALL=$((900 * TIMEOUT_SCALE)) # core deps: playwright + ddgs + mcp wheels
T_VENV=$((300         * TIMEOUT_SCALE)) # uv may download a CPython build
T_GIT=$((600          * TIMEOUT_SCALE)) # shallow clone; GIT_HTTP_LOW_SPEED_* is primary
T_UV_BOOT=$((300      * TIMEOUT_SCALE)) # uv self-installer
```

900s not `T_PIP`'s 300s: this stage pulls playwright's and ddgs's native wheels, and
unlike an optional stage a premature cut fails the install outright.

**Step 2: Guard the stdlib-pip path (`:400-404`)**

```bash
        run_with_timeout "$T_VENV" python -m venv "$INSTALL_DIR/venv" || {
            log_error "venv creation failed or timed out"; exit 1; }
        # shellcheck disable=SC1091
        . "$INSTALL_DIR/venv/bin/activate"
        _core_rc=0
        # Cosmetic; failing must not abort an otherwise-fine install.
        run_with_timeout "$T_CORE_INSTALL" pip install --upgrade pip >/dev/null 2>&1 || true
        run_with_timeout "$T_CORE_INSTALL" pip install --upgrade --force-reinstall -e . \
            >/dev/null 2>&1 || _core_rc=$?
        if [ "$_core_rc" -eq 124 ]; then
            log_error "pip install timed out after $((T_CORE_INSTALL / 60))m - a package download stalled."
            log_error "Retry on a slower link with: AGENT8088_TIMEOUT_SCALE=3"
            exit 1
        elif [ "$_core_rc" -ne 0 ]; then
            log_error "pip install failed (exit $_core_rc)"; exit 1
        fi
```

`source` → `.` so the line is not bash-only (Task C2 pins portability).

**Step 3: Guard the uv path (`:432-434`)**

```bash
        _core_rc=0
        run_with_timeout "$T_CORE_INSTALL" "$UV_CMD" pip install --python "$_py" \
            --reinstall-package agent8088 -e "$INSTALL_DIR" >/dev/null 2>&1 || _core_rc=$?
        if [ "$_core_rc" -eq 124 ]; then
            log_error "uv pip install timed out after $((T_CORE_INSTALL / 60))m - a package download stalled."
            log_error "Retry on a slower link with: AGENT8088_TIMEOUT_SCALE=3"
            log_error "Or see the underlying error with:"
            log_error "  $UV_CMD pip install --python $_py -e \"$INSTALL_DIR\""
            exit 1
        elif [ "$_core_rc" -ne 0 ]; then
            log_error "uv pip install failed; retrying with --reinstall"
            _core_rc=0
            run_with_timeout "$T_CORE_INSTALL" "$UV_CMD" pip install --python "$_py" \
                --reinstall -e "$INSTALL_DIR" >/dev/null 2>&1 || _core_rc=$?
            [ "$_core_rc" -ne 0 ] && { log_error "Failed to install agent8088 (exit $_core_rc)"; exit 1; }
        fi
```

**Step 4:** Wrap `git fetch` (`:376`) and `git clone` (`:383`) in
`run_with_timeout "$T_GIT"`, and the uv self-installer `sh` invocation (`:187` region)
in `run_with_timeout "$T_UV_BOOT"`. `GIT_HTTP_LOW_SPEED_*` is the primary defence; the
wrapper bounds a stall in the local object-write phase, which those variables miss.

**Step 5:** Wrap the optional stages (`:448-492`, npm, `ollama list`/`pull`) in
`run_with_timeout` + `warn_stage` + `record_skip`, mirroring
`$REF/install.sh:560-630,780-870`.

**Step 6: Verify**

```bash
bash -n install.sh && grep -c "run_with_timeout" install.sh
```
Expected ≥ 16.

**Commit:** `git add install.sh && git commit -m "fix(install): bound every network stage in install.sh"`

---

### Task B6: Guard the mandatory stages (PowerShell)

Mirror B5 across `Install-Uv` (`:336`), `Test-Python` (`:391`), `Install-Deps`
(`:622`, `:628`, `:636`), `Install-Gateway-Extras` (`:670`, `:680`, `:691`, `:694`),
`Install-Node-Bridge`, `Install-Embedding-Model` (`:859`+). Add
`$TCoreInstall = 900 * $TimeoutScale`, `$TVenv = 300 * …`, `$TExtract = 300 * …`,
`$TUvBoot = 300 * …`. Replace each `& $script:UvCmd … | Out-Null` with
`Invoke-WithTimeout`.

Switching away from `& … | Out-Null` also removes the `$ErrorActionPreference` dance
those lines need: the child's stderr goes to a pipe rather than PowerShell's error
stream, so uv's harmless “Using CPython…” banner can no longer be mistaken for a
`NativeCommandError`.

**Verify:** `grep -n 'UvCmd' install.ps1` → only `Invoke-WithTimeout -FilePath
$script:UvCmd` lines plus `Write-Err` hint strings.

**Commit:** `git add install.ps1 && git commit -m "fix(install): bound every network stage in install.ps1"`

---

# Part C — Portability

### Task C1: Shell and OS preflight (bash)

**Files:** Modify: `install.sh` — first executable code, **before `set -e`** and before
any array use

```bash
# ---------------------------------------------------------------------------
# Shell preflight -- must be the first executable code in the file
# ---------------------------------------------------------------------------
# This script uses bash arrays in many places. Under dash, busybox ash or zsh those
# are either a syntax error or silently wrong (zsh arrays are 1-indexed), and the
# failure surfaces hundreds of lines later as something unrelated. The documented
# invocation is `curl -fsSL <url> | bash`, but `| sh` is the reflex, so re-exec under
# bash rather than refusing -- and only refuse when there is no bash at all.
#
# Written in strict POSIX sh: it has to parse before we know what shell we are in.
if [ -z "${BASH_VERSION:-}" ]; then
    if command -v bash >/dev/null 2>&1; then
        # $0 is "sh"/"bash" (not a path) when piped, so re-execing $0 cannot work.
        if [ -f "$0" ]; then exec bash "$0" "$@"; fi
        echo "This installer needs bash. Re-run it as:" >&2
        echo "  curl -fsSL <url> | bash" >&2
        exit 1
    fi
    echo "ERROR: bash is required and was not found." >&2
    echo "  Alpine / busybox:  apk add bash" >&2
    echo "  Then:              curl -fsSL <url> | bash" >&2
    exit 1
fi

# bash 3.2 is the floor: stock macOS ships 3.2.57 and will not be upgraded. The script
# is written to that floor (no associative arrays, no ${x,,}, no mapfile); Task C2
# keeps it that way. Anything older predates `+=(` on arrays.
case "${BASH_VERSINFO[0]:-0}" in
    0|1|2) echo "ERROR: bash ${BASH_VERSION:-?} is too old; bash 3.2+ required." >&2; exit 1 ;;
esac
```

**Step 2: Make `detect_os` refuse an unknown OS** — the `*)` arm currently warns and
carries on, so FreeBSD fails confusingly several stages later:

```bash
        *)
            log_error "Unsupported operating system: $(uname -s)"
            log_info "Supported: Linux, macOS, WSL2. Windows uses install.ps1."
            log_info "On another Unix, install manually:  pip install agent8088"
            exit 1
            ;;
```

**Step 3: Stop `/etc/os-release` leaking into the installer's shell** — `. /etc/os-release`
defines `NAME`, `VERSION`, `ID`, `PRETTY_NAME` and more where they can collide:

```bash
                if [ -f /etc/os-release ]; then
                    DISTRO="$(. /etc/os-release 2>/dev/null && printf '%s' "${ID:-unknown}")"
                    DISTRO_VERSION="$(. /etc/os-release 2>/dev/null && printf '%s' "${VERSION_ID:-}")"
                else
```

**Step 4: Verify across shells**

```bash
cd /Users/tahawaheed/Documents/Agent8088-Features-added
for s in sh dash zsh; do command -v $s >/dev/null || continue
  echo "--- $s"; cat install.sh | $s 2>&1 | head -3; done
bash -n install.sh && echo "bash syntax OK"
```
Expected: each re-execs under bash (or prints the `| bash` instruction) — no syntax
errors, no `bad substitution`.

**Commit:** `git add install.sh && git commit -m "fix(install): preflight shell and OS gate with actionable errors"`

---

### Task C2: Lock in bash 3.2 portability with a lint

**Files:** Create: `scripts/check_installer_portability.sh`

```bash
#!/bin/bash
# Fails if install.sh uses a construct unavailable in bash 3.2 (stock macOS) or a
# GNU-only tool flag. Cheap to run, and the failure it prevents is a macOS-only syntax
# error that never reproduces on a Linux CI box.
set -e
target="${1:-install.sh}"
fail=0
check() {
    if grep -nE "$1" "$target" | grep -vE '^\s*[0-9]+:\s*#'; then
        echo "  ^^ $2"; fail=1
    fi
}
check 'declare -A|local -A'              "associative arrays need bash 4"
check '\$\{[A-Za-z_][A-Za-z0-9_]*,,\}'   '${x,,} lowercasing needs bash 4'
check '\$\{[A-Za-z_][A-Za-z0-9_]*\^\^\}' '${x^^} uppercasing needs bash 4'
check '\bmapfile\b|\breadarray\b'        "mapfile/readarray need bash 4"
check '\|&'                              "|& needs bash 4; use 2>&1 |"
check '\bsed -i\s|\bsed --in-place'      "sed -i differs on BSD/macOS; use a temp file"
check '\breadlink -f\b'                  "readlink -f is GNU-only; macOS lacks it"
check '\bgrep -P\b'                      "grep -P is GNU-only"
check '\bbase64 -w\b'                    "base64 -w is GNU-only"
[ "$fail" -eq 0 ] && echo "portability OK: $target" || exit 1
```

**Verify:** `bash scripts/check_installer_portability.sh install.sh` → `portability OK`.
A failure is a real finding — fix the installer, not the lint.

**Commit:** `git add scripts/check_installer_portability.sh && git commit -m "test(install): lint install.sh for bash 3.2 and BSD portability"`

---

### Task C3: CRLF self-defence

**Files:** Modify: `install.sh` (after the C1 guard); create `.gitattributes`

```bash
# A checkout made on Windows with core.autocrlf=true, then run under WSL or Git Bash,
# gives every line a trailing CR. bash reports that as `$'\r': command not found` on a
# line that looks fine, which sends people hunting the wrong bug. Guarded on
# BASH_SOURCE so the piped case, where there is no file to inspect, is a no-op.
case "$(head -1 "${BASH_SOURCE[0]:-/dev/null}" 2>/dev/null)" in
    *$'\r')
        echo "ERROR: this file has Windows (CRLF) line endings." >&2
        echo "  Fix:  perl -pi -e 's/\r\$//' \"${BASH_SOURCE[0]}\"" >&2
        echo "  Or:   curl -fsSL <url> | bash   (always LF)" >&2
        exit 1
        ;;
esac
```

`perl -pi -e`, not `sed -i` — the C2 lint forbids `sed -i` for the same BSD/GNU reason.

```
# install.sh must stay LF even on a Windows checkout: it is executed by bash under WSL
# and Git Bash, where a CR is a syntax error.
install.sh           text eol=lf
docker-entrypoint.sh text eol=lf
*.ps1                text eol=crlf
```

**Verify:** `git check-attr eol -- install.sh install.ps1` → `lf` and `crlf`.

**Commit:** `git add install.sh .gitattributes && git commit -m "fix(install): detect CRLF checkouts and pin line endings"`

---

### Task C4: Force TLS 1.2 — **before** the terminal gate

**Files:** Modify: `install.ps1` — after `$ProgressPreference` (`:29`)

Ordering is load-bearing: `Start-InstallerInWindowsTerminal` (`:230`) re-downloads the
installer over HTTPS from `raw.githubusercontent.com`, so the gate itself needs TLS 1.2
on a 5.1 host.

```powershell
# Windows PowerShell 5.1 on Windows 10 pre-1809 and Server 2016/2019 defaults
# ServicePointManager.SecurityProtocol to Ssl3, Tls -- TLS 1.0. Every host this
# installer touches (astral.sh, github.com, nodejs.org, raw.githubusercontent.com) has
# required TLS 1.2 since 2018, so on those systems the first HTTPS call dies with
# "Could not create SSL/TLS secure channel" -- a message naming TLS rather than the
# missing setting. This must run BEFORE Ensure-SupportedTerminal, whose relaunch path
# re-downloads this script with Invoke-RestMethod. PowerShell 7 negotiates via the OS
# and ignores this property, so it is a no-op there.
#
# -bor rather than assignment: leaving existing flags alone means a host that still
# requires TLS 1.0 (a corporate TLS-terminating proxy) does not break.
try {
    $wanted = [Net.SecurityProtocolType]::Tls12
    if ([enum]::GetNames([Net.SecurityProtocolType]) -contains 'Tls13') {
        $wanted = $wanted -bor [Net.SecurityProtocolType]::Tls13
    }
    [Net.ServicePointManager]::SecurityProtocol =
        [Net.ServicePointManager]::SecurityProtocol -bor $wanted
} catch {
    # A .NET too old to know Tls12, or a constrained host. A download needing it will
    # fail with its own TLS message rather than silently here.
}
```

**Verify:** Task F1 (`pwsh` asserts `Tls12` is present) + F2/T1 on real 5.1.

**Commit:** `git add install.ps1 && git commit -m "fix(install): enable TLS 1.2 before the terminal gate"`

---

### Task C5: PowerShell version floor — also before the gate

**Files:** Modify: `install.ps1` — after the C4 block

```powershell
# 5.1 is the floor. Ensure-SupportedTerminal below calls Get-AppxPackage and uses
# -notin, so on PS 3.0/4.0 it fails from inside the gate with a message about Appx
# rather than about the PowerShell version. Name it once, here, before anything else
# runs.
if ($PSVersionTable.PSVersion.Major -lt 5) {
    Write-Host "[X] PowerShell $($PSVersionTable.PSVersion) is too old." -ForegroundColor Red
    Write-Host "    Windows PowerShell 5.1 or PowerShell 7+ is required."
    Write-Host "    5.1 ships with Windows 10 / Server 2016+; otherwise install"
    Write-Host "    PowerShell 7: https://aka.ms/powershell"
    exit 1
}
```

The ISE is not handled here — the terminal gate already refuses it (no `WT_SESSION`),
and that gate is out of scope this round.

**Commit:** `git add install.ps1 && git commit -m "fix(install): preflight the PowerShell version floor"`

---

### Task C6: Proxy support in both installers

There is currently **zero** proxy handling (`grep -c proxy` → `0`, `0`), so every
download fails behind a corporate proxy with a generic connection error.

**Step 1: `install.sh` — normalisation, since curl/uv/pip already read the vars**

```bash
# curl, uv and pip all read HTTP_PROXY / HTTPS_PROXY / NO_PROXY already, so the work
# here is normalisation, not plumbing: curl prefers lowercase, Python's requests
# prefers uppercase, and a proxy set in only one case silently applies to only some
# tools.
for _pv in http_proxy https_proxy no_proxy; do
    _uv="$(printf '%s' "$_pv" | tr 'a-z' 'A-Z')"   # tr, not ${x^^}: bash 3.2
    eval "_lower=\${$_pv:-}"; eval "_upper=\${$_uv:-}"
    [ -n "$_lower" ] && [ -z "$_upper" ] && export "$_uv=$_lower"
    [ -n "$_upper" ] && [ -z "$_lower" ] && export "$_pv=$_upper"
done
# %%@* strips credentials: HTTPS_PROXY frequently carries user:password@host, and
# echoing it would put a secret in the terminal scrollback and any CI log.
[ -n "${HTTPS_PROXY:-}" ] && log_info "Using proxy: ${HTTPS_PROXY##*@}"
```

**Step 2: `install.ps1` — resolve one proxy object**

```powershell
# WebClient and Invoke-WebRequest do NOT read HTTP_PROXY the way curl does; on Windows
# the proxy normally comes from WinHTTP/IE settings, which a machine configured only
# via environment variables does not have.
$script:ResolvedProxy = $null
$proxyUrl = if ($env:HTTPS_PROXY) { $env:HTTPS_PROXY } elseif ($env:HTTP_PROXY) { $env:HTTP_PROXY } else { "" }
if ($proxyUrl) {
    try {
        $script:ResolvedProxy = New-Object System.Net.WebProxy($proxyUrl, $true)
        # An authenticating corporate proxy typically accepts the logged-in identity;
        # this avoids prompting for a password we must never handle.
        $script:ResolvedProxy.Credentials = [System.Net.CredentialCache]::DefaultNetworkCredentials
        if ($env:NO_PROXY) { $script:ResolvedProxy.BypassList = $env:NO_PROXY -split ',' }
        [System.Net.WebRequest]::DefaultWebProxy = $script:ResolvedProxy
        Write-Info "Using proxy: $($proxyUrl -replace '://[^@/]+@', '://***@')"
    } catch {
        Write-Warn "Could not parse proxy '$proxyUrl' - continuing without it"
    }
}
```

The `-replace` masks credentials for the same reason as `%%@*` above.

**Verify:** `bash -n install.sh`; `grep -c ResolvedProxy install.ps1` ≥ 4.

**Commit:** `git add install.sh install.ps1 && git commit -m "feat(install): honour HTTP_PROXY/HTTPS_PROXY/NO_PROXY on both platforms"`

---

### Task C7: Windows long-path handling

The install root is
`%LOCALAPPDATA%\agent8088\agent8088\src\agent8088\gateway\platforms\whatsapp_bridge\`
— roughly 120 characters before `node_modules` begins nesting. npm trees routinely add
150+, so without `LongPathsEnabled` the install fails partway with `ENAMETOOLONG` or
`EPERM`, and the error names a package rather than the cause.

**Files:** Modify: `install.ps1` — in `Install-Node-Bridge`, before the npm call

```powershell
    # MAX_PATH is 260 unless LongPathsEnabled is on (Windows 10 1607+, off by default).
    # Detect it and keep the tree flat when it is off -- cheaper and far likelier to
    # succeed than asking for a registry change that needs admin and a reboot.
    $longPaths = 0
    try {
        $longPaths = (Get-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem' `
            -Name 'LongPathsEnabled' -ErrorAction Stop).LongPathsEnabled
    } catch { $longPaths = 0 }

    $npmArgs = @("install", "--no-audit", "--no-fund")
    if ($longPaths -ne 1) {
        Write-Info "Long paths are disabled; installing the bridge with a flat node_modules tree."
        $npmArgs += @("--install-strategy=hoisted")
        if ($bridgeDir.Length -gt 150) {
            Write-Warn "Bridge path is $($bridgeDir.Length) chars - npm may still hit the 260-char limit."
            Write-Warn "Enable long paths (admin, one time), then rerun the installer:"
            Write-Warn "  Set-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem' LongPathsEnabled 1"
        }
    }
```

Pass `$npmArgs` to the `Invoke-WithTimeout` npm call from B6.

**Verify:** F2/T13. **Commit:**
`git add install.ps1 && git commit -m "fix(install): handle Windows MAX_PATH for the WhatsApp bridge"`

---

# Part D — Issue 3: a Hermes-aligned ddgs fallback

`src/agent8088/web_search.py` is unchanged on this branch, so line numbers hold.

### Task D1: Retry the two Hermes source files

```bash
for f in agent/web_search_provider.py agent/web_search_registry.py; do
  curl -sL --max-time 30 -o "/tmp/hermes-$(basename $f)" \
    "https://raw.githubusercontent.com/nousresearch/hermes-agent/main/$f" \
    -w "$f http=%{http_code}\n"
done
grep -niE "ddgs|DDGS|backend|ratelimit|sleep|proxy|timeout" /tmp/hermes-web_search_provider.py | head -40
```

Both returned **429** at plan time. If they 429 again, proceed — D2–D6 already implement
the four documented behaviours — and record the gap in the commit message. If they
succeed and Hermes differs, prefer Hermes for anything it settles.

---

### Task D2: Derive the engine → host mapping from the installed package

Ground truth **before** the allowlist. The egress check fails closed, so a missing host
is a silent policy bypass and a wrong host a false denial. Do not guess.

```bash
python3 -m venv /tmp/ddgs-probe && /tmp/ddgs-probe/bin/pip install -q 'ddgs>=9,<10'
/tmp/ddgs-probe/bin/python - <<'PY'
import pathlib, re, ddgs
print("ddgs", getattr(ddgs, "__version__", "?"))
root = pathlib.Path(ddgs.__file__).parent
pat = re.compile(r'https?://[A-Za-z0-9._~:/?#\[\]@!$&\'()*+,;=%-]+')
for p in sorted(root.rglob("*.py")):
    hosts = {m.split("/")[2] for m in pat.findall(p.read_text(errors="replace"))}
    if hosts:
        print(f"{p.relative_to(root)}: {sorted(hosts)}")
PY
```

Record in `.claude/plans/ddgs-engine-hosts.md`. **Task D6 uses that file, not the
placeholder in this plan.**

**Commit:** `git add .claude/plans/ddgs-engine-hosts.md && git commit -m "docs: record ddgs engine host mapping from installed 9.x"`

---

### Task D3: Failing test — detection must reject an unimportable ddgs

**Files:** Create: `tests/test_web_search_ddgs_detection.py`
(flat `tests/test_*.py`, matching this repo's convention)

```python
"""ddgs availability must reflect importability, not directory presence.

find_spec() answers "is there a module of this name on sys.path", which stays True for
a distribution whose Python files landed but whose native dependency did not. That is
the reported hit-and-miss detection: /search doctor prints "ddgs importable: yes" and
every search then dies on ImportError.
"""
import builtins
import pytest

from agent8088 import web_search


@pytest.fixture(autouse=True)
def _clear_cache():
    web_search._ddgs_import_state = None
    yield
    web_search._ddgs_import_state = None


def test_reports_unavailable_when_import_raises(monkeypatch):
    real_import = builtins.__import__

    def boom(name, *args, **kwargs):
        if name == "ddgs" or name.startswith("ddgs."):
            raise ImportError("libprimp.so: cannot open shared object file")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", boom)
    assert web_search._ddgs_installed() is False


def test_reports_available_when_import_succeeds():
    assert web_search._ddgs_installed() is True


def test_result_is_cached(monkeypatch):
    calls = []
    real_find = web_search.importlib.util.find_spec

    def counting(name, *a, **k):
        calls.append(name)
        return real_find(name, *a, **k)

    monkeypatch.setattr(web_search.importlib.util, "find_spec", counting)
    web_search._ddgs_installed()
    web_search._ddgs_installed()
    assert calls.count("ddgs") == 1
```

**Run:** first and third FAIL. **Commit the RED test.**

---

### Task D4: Make detection a real import probe

**Files:** Modify: `src/agent8088/web_search.py:415-416`

```python
# Cached tri-state: None = not yet probed, True/False = the answer.
_ddgs_import_state: bool | None = None


def _ddgs_installed() -> bool:
    """Is ddgs actually importable right now?

    find_spec() was the wrong test and is why detection was hit-and-miss: it answers
    "is there a module of this name on sys.path", which stays True for a distribution
    whose Python files landed but whose compiled dependency did not. ddgs pulls in a
    native extension, and a wheel-less interpreter version, a partially rolled-back
    install, or an ABI mismatch all leave ddgs/ present with `import ddgs` raising --
    find_spec says yes, /search doctor prints "ddgs importable: yes", and the search
    then fails on the very ImportError this check exists to prevent.

    So import the symbol the provider actually calls. find_spec stays as a cheap
    pre-filter: it avoids paying for an import attempt in the common genuinely-absent
    case. Cached because is_available() calls this on every search and the doctor table
    calls it again.
    """
    global _ddgs_import_state
    if _ddgs_import_state is None:
        if importlib.util.find_spec("ddgs") is None:
            _ddgs_import_state = False
        else:
            try:
                from ddgs import DDGS  # noqa: F401
                _ddgs_import_state = True
            except Exception:  # noqa: BLE001 -- ImportError, OSError from a bad .so, anything
                _ddgs_import_state = False
    return _ddgs_import_state
```

**Run:** `3 passed`. **Commit.**

---

### Task D5: Failing test — engines, throttle spacing, retry, cache, context manager

**Files:** Create: `tests/test_web_search_ddgs_provider.py`

Covers: explicit `backend=` (not `auto`); `max_results` as a **keyword** (Hermes' hard
constraint); `DDGS` used as a **context manager**; a minimum interval enforced *between*
searches (Hermes' “short delay”); `RatelimitException` retried then succeeding;
exhausted retries reported as a rate limit; `"202"` in an unrelated message **not**
reported as a rate limit; a repeat query served from cache; a blocked engine dropped
rather than fatal; every engine blocked failing closed and non-retryable;
`_DDGS_ENGINE_HOSTS` covering every ordered engine. Full source as in the previous
revision, with `FakeCtx` supplying `check_url` / `get_secret` / `wrap` / `config`.

Key assertions to keep verbatim:

```python
def test_uses_context_manager_and_keyword_max_results():
    """Hermes documents both: `with DDGS(...) as ddgs` so the HTTP client is closed,
    and max_results ALWAYS as a keyword argument."""
    import inspect
    src = inspect.getsource(web_search._ddgs_text)
    assert "with DDGS(" in src, "DDGS must be used as a context manager"
    assert "max_results=" in src, "max_results must be passed as a keyword"


def test_spaces_consecutive_searches(monkeypatch):
    """Hermes: 'DuckDuckGo may throttle after many rapid requests. Add a short delay
    between searches.' The cache covers repeats; this covers distinct queries."""
    slept = []
    monkeypatch.setattr(web_search, "_DDGS_MIN_INTERVAL", 2.0)
    monkeypatch.setattr(web_search.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(web_search, "_ddgs_text",
                        lambda q, l, *, backend, timeout, proxy=None: _hit())
    provider = DdgsProvider()
    provider.search("first", 5, FakeCtx())
    provider.search("second", 5, FakeCtx())
    assert any(s > 0 for s in slept), "no delay inserted between distinct searches"
```

**Run:** all 11 FAIL. **Commit the RED tests.**

---

### Task D6: Implement the ddgs rewrite

**Files:** Modify: `src/agent8088/web_search.py` — imports (`:33-41`), ddgs section
(`:406-486`)

Add `import time`. Replace the constants block with `_DDGS_ENGINE_HOSTS` (from Task D2,
**not** the placeholder), `_DDGS_ENGINE_ORDER`, a derived `_DDGS_HOSTS` union,
`_DDGS_TIMEOUT = 10`, `_DDGS_MIN_INTERVAL = 2.0` + `_ddgs_last_call`,
`_DDGS_ATTEMPTS = 3` + `_DDGS_BACKOFF = (0.0, 1.5, 4.0)`, and the 300s/32-entry cache
with `_ddgs_cache_get` / `_ddgs_cache_put` / `_ddgs_wait_turn` / `_ddgs_throttle_errors`.
Then:

```python
def _ddgs_text(query: str, limit: int, *, backend: str, timeout: int, proxy=None):
    """Call the ddgs library. Isolated so tests can patch it without importing the
    package.

    Two details are Hermes' documented requirements rather than preference: DDGS is
    used as a CONTEXT MANAGER so its HTTP client and connections are closed (the
    previous code built a bare DDGS() per search and never closed it, leaking a client
    per call), and max_results is passed as a KEYWORD argument.
    """
    from ddgs import DDGS

    with DDGS(timeout=timeout, proxy=proxy) as ddgs:
        return ddgs.text(query, max_results=limit, backend=backend)
```

Add `_ddgs_allowed_engines(ctx)` — per-engine egress filtering, because the old
all-or-nothing rule would have made a wider engine list *more* likely to be denied —
and `_ddgs_proxy(ctx)`, which reads `search_proxy` and routes it through
`ctx.check_url` so a policy-forbidden proxy is dropped rather than honoured. Then
rewrite `DdgsProvider.search` to: check importability → filter engines (fail closed
and non-retryable if none) → serve from cache **before** any throttle wait → loop
`_DDGS_ATTEMPTS` with backoff + `_ddgs_wait_turn()` → `continue` only on typed
throttles, `break` on other failures, treat “no results found” as success-with-nothing.
Full source as in the previous revision.

**Run:** `python3 -m pytest tests/test_web_search_ddgs*.py -v` → `14 passed`. **Commit.**

---

### Task D7: Document `search_proxy`, refresh stale docs, extend `/search doctor`

**Files:** Modify: `src/agent8088/web_search.py:11,24,45-48`;
`src/agent8088/config.txt:220,244,291`; `src/agent8088/cli.py:2606-2607`

- Add a `search_proxy=` block to `config.txt`, stating it applies to ddgs only and
  passes through the egress policy.
- Replace “the one that rate-limits under normal use” with the rotation + retry +
  5-minute repeat-cache behaviour.
- Doctor table:

```python
        _ddgs_engines, _ddgs_block = A.web_search._ddgs_allowed_engines(
            A._search_context())
        t.add_row("ddgs engines allowed",
                  ", ".join(_ddgs_engines) if _ddgs_engines
                  else f"[red]none — {_ddgs_block}[/red]")
```

**Verify:** `grep -n "rate.limit\|ratelimit\|202" src/agent8088/web_search.py
src/agent8088/config.txt` → only the new wording plus `_ddgs_throttle_errors`'s
explanation of the old bug. **Commit.**

---

# Part E — Issue 2: setup on every run

### Task E1: Always run first-run setup (bash) — and fix the test that pins the old gate

**Files:** Modify: `install.sh:1216-1243`; `tests/test_installer_initial_setup.py`

**Step 1: Replace `run_initial_setup`**

```bash
# Runs on EVERY invocation, not only on a fresh install.
#
# The removed gate was `[ "$FRESH_INSTALL" != true ] && [ "$CONFIG_CREATED" != true ]`,
# which skipped setup on any re-run over an existing install with an existing config.
# That is exactly the case where setup matters most: a run where an optional stage
# failed still installs a working agent, and the user is left with no prompt for
# working directory, model, or web search -- and no indication that `agent8088 --setup`
# is the thing to run. Two gates remain, both because the prompt is physically
# impossible, not because it is unwanted:
#   --skip-setup   an explicit request, honoured
#   no /dev/tty    nothing to read from; the message names the manual command
run_initial_setup() {
    if [ "$SKIP_SETUP" = true ]; then
        log_info "Skipping first-run setup (--skip-setup)"
        log_info "Configure later with: agent8088 --setup"
        return 0
    fi
    if [ "$IS_INTERACTIVE" = false ] && ! (: </dev/tty) 2>/dev/null; then
        log_info "No TTY detected — skipping setup"
        log_info "Run agent8088 --setup later to configure your model."
        return 0
    fi

    # Prefer the shim, fall back to the venv interpreter. setup_path runs before us,
    # yet a PATH-link directory that is not writable leaves no shim -- and skipping
    # setup because a symlink is missing, when the module is right there and
    # importable, is the wrong trade.
    local _cmd=() _shim="$(get_command_link_dir)/agent8088"
    local _py="$INSTALL_DIR/venv/bin/python"
    if [ -x "$_shim" ]; then
        _cmd=("$_shim" --setup)
    elif [ -x "$_py" ]; then
        log_warn "agent8088 shim not found; running setup via the venv interpreter"
        _cmd=("$_py" -m agent8088.cli --setup)
    else
        log_warn "agent8088 is not runnable yet; run agent8088 --setup later."
        record_skip "First-run setup" "agent8088 not runnable" "agent8088 --setup"
        return 0
    fi

    log_info "Starting setup..."
    if run_agent8088_command "${_cmd[@]}"; then
        INITIAL_SETUP_RAN=true
    else
        log_warn "Setup did not complete; run agent8088 --setup later."
        record_skip "First-run setup" "did not complete" "agent8088 --setup"
    fi
}
```

`record_skip` on both failure paths is much of the point: a skipped setup now appears in
the end-of-run summary instead of scrolling away.

**Step 2:** Confirm `run_agent8088_command` exists (it feeds the child `< /dev/tty`,
which is what makes prompting work under `curl | bash`). If absent, port from
`$REF/install.sh:1395-1404`.

**Step 3: Verify**

```bash
bash -n install.sh && grep -n FRESH_INSTALL install.sh
```
Expected: `:43`, `:386`, `:709`, and `launch_initial_agent` — but **not** inside
`run_initial_setup`.

**Step 4: Update the test that pins the removed gate**

`tests/test_installer_initial_setup.py` currently asserts
`(fresh=False, config=False) → "Existing installation and config found"`. That string no
longer exists. Change that case to expect the non-interactive skip message, like the
other two, and add a case proving setup is attempted when a TTY is present. Same commit
— a red suite between commits is worse than a slightly larger one.

**Step 5: Commit**

```bash
git add install.sh tests/test_installer_initial_setup.py
git commit -m "fix(install): run first-run setup on every invocation"
```

---

### Task E2: Always run first-run setup (PowerShell)

**Files:** Modify: `install.ps1:1192-1219`; `tests/test_installer_initial_setup.py`

Drop the `-not $script:FreshInstall -and -not $script:ConfigCreated` gate; keep
`$SkipSetup` and `$NonInteractive`; add a `venv\Scripts\python.exe -m agent8088.cli
--setup` fallback; call `Register-SkippedStage` on both failure paths. Mirror E1's
structure and comments.

`Run-InitialSetup` must stay **outside** `Invoke-WithTimeout`: setup is interactive and
reads the console, so bounding it would kill a user mid-answer.

**Do not** change `$NonInteractive`'s definition this round. On this branch it is
`-not [Environment]::UserInteractive` (`:130`), and the out-of-scope terminal gate
*also* reads it (`Ensure-SupportedTerminal` refuses non-interactive runs). Changing its
meaning would alter that gate's behaviour, which you asked me to leave alone. Noted as
Open Question 5.

Keep the function's brace style `^function Run-InitialSetup {` … `^}$` so
`_powershell_function` still finds it.

**Verify:** `grep -n FreshInstall install.ps1` → `:86`, `:597`, and
`Start-InitialAgent` — but **not** in `Run-InitialSetup`. Then
`python3 -m pytest tests/test_installer_initial_setup.py -v` → passes (with `pwsh` now
installed, these actually run).

**Commit:** `git add install.ps1 tests/test_installer_initial_setup.py && git commit -m "fix(install): run first-run setup on every invocation (Windows)"`

---

### Task E3: Delete the dead setup wizards

**Files:** Modify: `install.ps1` (`Run-SetupWizard`, `Select-ModelProvider`,
`Read-SecretValue`, the `$Builtin*` tables); `install.sh` (`run_setup_wizard`,
`read_setup_value`, `read_secret_setup_value`, the provider tables)

**Step 1: Confirm unreachable** — every hit is a definition or a call from *inside*
another dead function; nothing from `main` / the `# Main` block.

**Step 2: Delete, keeping the live helpers.** `run_agent8088_command` **is** live — E1
calls it. Re-grep after deleting.

**Step 3: Verify**

```bash
bash -n install.sh
bash scripts/check_installer_portability.sh install.sh
python3 - <<'PY'
import re, pathlib
src = pathlib.Path("install.ps1").read_text(encoding="utf-8")
for fn in ("Run-InitialSetup", "Invoke-WithTimeout", "Invoke-BoundedDownload",
           "Ensure-SupportedTerminal", "Test-SupportedTerminalHost"):
    assert re.search(rf"(?ms)^function {re.escape(fn)} \{{.*?^\}}$", src), fn
print("live functions intact and extractable")
PY
python3 -m pytest tests/ -q
```
Expected: `live functions intact and extractable`, suite green.

**Commit:** `git add install.sh install.ps1 && git commit -m "refactor(install): remove the dead setup wizards"`

---

# Part F — Verification

### Task F1: Extend the repo's installer test convention

**Files:** Create: `tests/test_installer_timeouts.py`;
`tests/support/Start-Tarpit.ps1`; `tests/test_installer_shell_preflight.sh`

`tests/test_installer_timeouts.py` follows `test_installer_terminal.py` exactly —
`_powershell_function(name)` + `_run_powershell(command)` — and asserts:

| Assertion | Runs on macOS `pwsh` 7? |
|---|---|
| `Invoke-WithTimeout` kills a hang **and returns in ~3s, not 30s** | yes |
| a non-zero child exit surfaces as itself, not 0 | yes |
| `Invoke-BoundedDownload` returns `TimedOut` on a tarpit, within ~5s, and deletes the partial file | yes |
| a real download succeeds | yes |
| TLS 1.2 present in `SecurityProtocol` after the C4 block | **no — 7.x ignores it; 5.1 only** |
| the 5.1 manual-argv-quoting branch with a spaced path | **no — needs Windows PowerShell 5.1** |

Mark the last two `@pytest.mark.skipif` on `$PSVersionTable.PSEdition -ne 'Desktop'`,
so a macOS run reports them skipped rather than passing vacuously. **A vacuous pass is
worse than a skip** — it reads as coverage that does not exist.

Watch the clock on the timeout assertions: a "pass" at 30s means the call returned on
the child's own exit, not on the timeout.

`Start-Tarpit.ps1` accepts, sends `200 OK` with a large `Content-Length`, then sleeps.
**Without it the body-stall case is untested** — it is what proves fix 1b was real.

`test_installer_shell_preflight.sh` covers the bash side: `run_with_timeout` on a
TERM-ignoring child returns 124 in ~12s; `_timeout_supports_k` probes without executing
the wrong command; the fallback watchdog works with `timeout` masked
(`PATH=/nonexistent`), so macOS's no-`timeout` case is exercised on Linux too.

**Verify:** `python3 -m pytest tests/test_installer_timeouts.py -v` — real passes, not
skips, for the four `pwsh`-capable rows.

**Commit:** `git add tests/ && git commit -m "test(install): timeout, bounded-download, and tarpit coverage"`

---

### Task F2: Windows matrix

Scoped to what the terminal gate permits. `powershell.exe` is 5.1; `pwsh.exe` is 7.x.

| # | Host | Command | Expectation |
|---|---|---|---|
| M1 | 5.1, Windows Terminal | `python -m pytest tests/test_installer_timeouts.py -v` | all rows pass, incl. the two 5.1-only |
| M2 | 7.x, Windows Terminal | same | 5.1-only rows skip |
| M3 | 5.1, Windows Terminal | `.\install.ps1` — fresh machine | completes |
| M4 | 5.1, Windows Terminal | `.\install.ps1` again | **setup prompts again** |
| M5 | 7.x, Windows Terminal | `.\install.ps1`, then again | same |
| M6 | 5.1, Windows Terminal | `iex (irm <raw-url>/install.ps1)` | the documented path works |
| M7 | either | `.\install.ps1 -SkipSetup` | setup does **not** prompt |
| M8 | Windows ARM64 | M3 | `Get-WindowsArch` picks the arm64 Git asset |
| M9 | **conhost** | `.\install.ps1` | **expected: refused by the gate.** Record the message; do not "fix" it |
| M10 | VS Code terminal | M3 | gate allows via `$env:TERM_PROGRAM` |

Checks:

- **T1 (TLS)** — M1 prints `Tls12`. On Server 2016 / Win10 pre-1809 the gate blocks the
  install before C4 can be observed end-to-end, so verify C4 in isolation instead:
  extract the block with `_powershell_function`-style regex and run it under 5.1.
  See Open Question 1.
- **T2 (timeout fires)** — M1/M2, watching the clock.
- **T3 (body stall)** — start `Start-Tarpit.ps1` in a second tab, set
  `AGENT8088_TARPIT_URL`, re-run. **Confirm this fails before Task B3.**
- **T4 (stuck package — the headline symptom)** — elevated:
  `New-NetFirewallRule -DisplayName a8088-block -Direction Outbound -RemoteAddress 151.101.0.0/16 -Action Block`,
  then M3. Expect *"uv pip install timed out after 15m — a package download stalled"*
  and a **non-zero exit**, not a frozen console. Remove with
  `Remove-NetFirewallRule -DisplayName a8088-block`. To shorten the wait, lower
  `$TCoreInstall` for this cell only — `AGENT8088_TIMEOUT_SCALE` *multiplies*.
- **T5 (setup always runs)** — M4/M5: working-directory / provider / model / web-search
  prompts must appear on the **second** run. Acceptance test for Issue 2.
- **T6 (partial failure still prompts)** — before M4, break one optional stage (rename
  `%LOCALAPPDATA%\agent8088\node`, or block `registry.npmjs.org`). Expect the bridge in
  the skipped-components summary **and** setup still prompting.
- **T7 (spaces in the profile path)** — run M3 as a user whose `%USERPROFILE%` contains
  a space. Exercises the 5.1 manual quoting and the 8.3 normalisation.
- **T9 (ExecutionPolicy)** — with `Restricted`, `.\install.ps1` is refused but
  `iex (irm …)` and `-ExecutionPolicy Bypass -File` work.
- **T10 (ddgs)** — post-install `/search doctor`: `ddgs importable: yes` **and**
  `ddgs engines allowed: duckduckgo, brave, …`. Then three *different* `web_search`
  calls back to back — none should rate-limit (what `_DDGS_MIN_INTERVAL` buys). Then
  repeat one query verbatim — instant, from cache.
- **T11 (non-ASCII path)** — `$env:AGENT8088_HOME = "$env:LOCALAPPDATA\代理8088"`, then M3.
- **T13 (long paths)** — with `LongPathsEnabled = 0`, confirm the bridge installs or
  warns precisely; set it to 1 and confirm the warning disappears.
- **T14 (proxy)** — behind an authenticating proxy with only `HTTPS_PROXY` set and no IE
  settings: downloads succeed and **no credential appears in the output**.

### Task F3: macOS / Linux matrix

| # | Target | Command |
|---|---|---|
| L1 | macOS (bash 3.2) | `bash tests/test_installer_shell_preflight.sh`, then a real install ×2 |
| L2 | Ubuntu 24.04 container | same |
| L3 | `sh` / dash / busybox | `cat install.sh \| sh` — re-execs or refuses cleanly (C1) |
| L4 | Alpine, no bash | `cat install.sh \| sh` — prints `apk add bash` |
| L5 | WSL2 | real install ×2 |
| L6 | Linux arm64 | real install |

```bash
cd /Users/tahawaheed/Documents/Agent8088-Features-added
bash -n install.sh
bash scripts/check_installer_portability.sh install.sh
command -v shellcheck && shellcheck -S warning install.sh
python3 -m pytest tests/ -q
bash tests/test_installer_shell_preflight.sh
```

Two consecutive real installs in a clean container, for Issue 2 on the bash side:

```bash
docker run --rm -it ubuntu:24.04 bash -c '
  apt-get update -qq && apt-get install -y -qq curl git ca-certificates >/dev/null
  curl -fsSL https://raw.githubusercontent.com/tayyabimam1/Agent8088-Features-added/development/install.sh -o /tmp/i.sh
  bash /tmp/i.sh </dev/tty; echo "--- second run: setup must prompt again ---"; bash /tmp/i.sh </dev/tty'
```

### Task F4: Record results

`.claude/plans/2026-08-19-compat-matrix-results.md`, one row per cell: cell, shell, host,
arch, PASS/FAIL, notes. Commit it. **A cell with no recorded result is an untested
cell — say so rather than inferring from a sibling.**

---

## Files changed

| File | Tasks |
|---|---|
| `install.sh` | A1, B1, B4, B5, C1, C2, C3, C6, E1, E3 |
| `install.ps1` | A2, B2, B3, B4, B6, C4, C5, C6, C7, E2, E3 |
| `.gitattributes` | C3 (new) |
| `scripts/check_installer_portability.sh` | C2 (new) |
| `src/agent8088/web_search.py` | D4, D6, D7 |
| `src/agent8088/config.txt` | D7 |
| `src/agent8088/cli.py` | D7 |
| `tests/test_web_search_ddgs_detection.py` | D3 (new) |
| `tests/test_web_search_ddgs_provider.py` | D5 (new) |
| `tests/test_installer_timeouts.py` | F1 (new) |
| `tests/test_installer_shell_preflight.sh` | F1 (new) |
| `tests/support/Start-Tarpit.ps1` | F1 (new) |
| `tests/test_installer_initial_setup.py` | **E1, E2 (modified — it pins the old gate)** |
| `.claude/plans/ddgs-engine-hosts.md` | D2 (new) |
| `.claude/plans/hermes-ddgs-reference.md` | D1 (new, if the fetch succeeds) |
| `.claude/plans/2026-08-19-compat-matrix-results.md` | F4 (new) |

**Not touched:** `Ensure-SupportedTerminal`, `Test-SupportedTerminalHost`,
`Install-WindowsTerminal`, `Start-InstallerInWindowsTerminal`,
`tests/test_installer_terminal.py` — out of scope by your decision.

## Risks and tradeoffs

| Risk | Mitigation |
|---|---|
| **The terminal gate keeps conhost/Server/pre-1903 blocked regardless of this work.** | Recorded as “blocked upstream” in the contract, not silently claimed as supported. Reopen via Open Question 1. |
| **Part A ports code from a different repo whose surrounding context differs.** | Port function bodies, not line ranges; A1 Step 4 and A2 Step 5 verify each symbol resolves and is regex-extractable before anything calls it. |
| **New PowerShell functions may not match `_powershell_function`'s regex.** | A2 Step 3 and E3 Step 3 assert extractability. Brace style is a hard requirement, not a preference. |
| **`Part E` breaks an existing green test.** | E1 Step 4 updates it in the same commit. |
| **Widening ddgs egress to ~10 hosts enlarges the network surface.** You approved this. | Per-engine filtering keeps `ssrf_allow_hosts` in charge — and unlike today, a policy permitting one engine now *works* instead of denying everything. The host set stays closed and enumerated. |
| **A wrong or missing host in `_DDGS_ENGINE_HOSTS` is a silent policy bypass.** | D2 derives it from the installed package; `test_engine_host_map_covers_every_ordered_engine` fails on a missing entry. Re-run D2 on every ddgs bump. |
| **`_DDGS_MIN_INTERVAL = 2.0` adds up to 2s to a search.** | Only when two searches land within 2s, which a model rarely does — it must read each result first. Tunable; 0 disables. |
| **`search_proxy` is a new egress destination.** | Routed through `ctx.check_url`, so a policy-forbidden proxy is dropped. Off unless set. |
| **900s `T_CORE_INSTALL` is a long wait before the message.** | It must exceed a genuine slow-link install of playwright + ddgs wheels, or the fix becomes a new failure mode. `AGENT8088_TIMEOUT_SCALE` raises it; T4 lowers it for testing. |
| **`WebClient` is obsolete in .NET Core.** | Obsolete, not removed; `DownloadFileTaskAsync` is the only API with one code path on 5.1 and 7.x. M1/M2 cover both. |
| **macOS `pwsh` 7 cannot exercise 5.1-only paths.** | Those assertions are explicitly `skipif`-ed rather than allowed to pass vacuously, and M1 covers them on real Windows. |
| **C1's `exec bash "$0"` cannot work for a piped script.** | Handled: re-execs only when `$0` is a real file, otherwise prints the `\| bash` instruction. L3/L4 test both. |
| **Setup on every run changes behaviour for scripted installs.** | `--skip-setup` / `-SkipSetup` and the no-TTY path still exit early, so CI and `curl \| bash` are unaffected. |
| **Deleting the wizards (E3) removes ~300 lines.** | Verified unreachable first; E3 Step 3 asserts the live functions survive. Recoverable from git. |

## Open questions

1. **Reopen the terminal gate later?** It is the single thing standing between this work
   and the “legacy PowerShell / any system” goal in the original brief. If you want it
   softened to a warning, that is a small, self-contained follow-up — worth a word with
   `d296ab5`'s author (Saad Hussain Kazmi) first, since it was a deliberate decision.
2. **Branch name for this work?** The plan assumes a branch off `development@d296ab5`.
   Confirm a name, or say to commit straight onto `development`.
3. **Should `PREFERENCE` move ddgs last, matching Hermes?** Hermes orders
   `tavily → exa → parallel → firecrawl → searxng → brave-free → ddgs`; this repo has
   `("searxng", "ddgs", "tavily", "exa")`. One line, real behaviour consequence, so it
   is not in any task.
4. **Is Termux still a target?** `is_termux` exists in `detect_os` and upstream docs say
   support was dropped. C1 leaves it detected-but-allowed; it should either be supported
   and tested, or refused.
5. **`$NonInteractive` semantics.** `-not [Environment]::UserInteractive` reports
   non-interactive inside a service or scheduled task but says nothing about whether a
   console is attached to `iex (irm …)`. An explicit `AGENT8088_NONINTERACTIVE` opt-out
   would be narrower and honest — but the out-of-scope terminal gate also reads this
   variable, so changing it would alter that gate's behaviour. Deferred.
6. **Should `Start-InitialAgent` / `launch_initial_agent` also become unconditional?**
   Both stay gated on `FreshInstall`, so a re-run prompts for setup but does not
   re-launch the agent.
