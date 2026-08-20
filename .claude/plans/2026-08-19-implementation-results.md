# Implementation results — installer hardening

Branch `fix/installer-hardening-timeouts-setup-ddgs`, rebased onto
`origin/development@782bf37`. 11 commits. Plan:
[2026-08-19_143000-universal-installer-hardening.md](2026-08-19_143000-universal-installer-hardening.md)

## Timeout budgets — as agreed

| Step | Size | Limit | bash | PowerShell |
|---|---|---|---|---|
| Is Ollama alive? (`ollama list`) | nothing, local | **15 sec** | `T_OLLAMA_CHECK` | `$TOllamaCheck` |
| Ollama embed model pull | 274 MB | **10 min** | `T_OLLAMA_PULL` | `$TOllamaPull` |
| WhatsApp npm install | 142 small packages | **5 min** | `T_NPM` | `$TNpm` |
| Chromium (Playwright) | ~150 MB | **10 min** | `T_CHROMIUM` | `$TChromium` |
| Node download | ~30 MB | **3 min** | `T_NODE_DL` | `$TDownload` |
| Gateway extras (Python) | tens of MB | **5 min** | `T_PIP` | `$TPip` |
| Git clone | small | **10 min** | `T_GIT` | *(via `GIT_HTTP_LOW_SPEED_*`)* |

Three budgets were not in the table and are stated so they are reviewable:
`T_CORE_INSTALL`/`$TCoreInstall` = **10 min** (the stage that was reported hanging —
the largest download set here, so it takes the same ceiling as Chromium rather than a
looser one), `T_VENV`/`$TVenv` = 5 min, `T_UV_BOOT`/`$TUvBoot` = 5 min,
`$TExtract` = 5 min. All scale with `AGENT8088_TIMEOUT_SCALE`.

Both tables are asserted by `tests/test_installer_timeouts.py` so a later edit cannot
drift them silently.

## What is proven, and how

### Issue 1 — hangs

`grep -i timeout` over either installer returned **nothing** at the branch point.
There was no timeout to fix; there was one to build.

- **`Invoke-WebRequest -TimeoutSec` does not bound the body.** Measured against a
  server that completes the handshake, sends `200 OK` with a large `Content-Length`,
  then emits one byte every 5s:
  `Invoke-WebRequest -OutFile -TimeoutSec 5` was **still running at 30s** and had to
  be killed externally. `Invoke-BoundedDownload -TimeoutSec 5` returned
  `TimedOut=True` at 5s and deleted the partial file. That server is now
  `tests/support/tarpit.py`.
- **`timeout` without `-k` loses to a TERM-ignoring child.** `run_with_timeout 2` on
  `trap "" TERM; sleep 60` returns **124 at ~12s** on both dispatch paths — GNU
  `timeout -k` (ubuntu:24.04) and the fallback watchdog (macOS, which ships no
  `timeout(1)` at all, so the local machine exercises that path by default).
- **Exit codes are real.** `exit 7` surfaces as 7, not 0 — the bug the
  `System.Diagnostics.Process` rewrite exists to prevent.
- **A chatty child cannot deadlock.** 20 000 lines of stdout complete because both
  pipes are drained asynchronously.
- **curl aborts a dead handshake** at the connect timeout instead of hanging
  (verified against `10.255.255.1`).

### Issue 2 — setup must always run

Real double install in a clean `ubuntu:24.04` container, both runs `exit 0`. Run 2
now reports **`No TTY detected — skipping setup`** where it previously reported
`Existing installation and config found`. With a TTY that difference is the prompt
appearing on a re-run, which is the acceptance criterion.

`tests/test_installer_initial_setup.py` asserted the *old* behaviour and was
rewritten: the four `(FRESH_INSTALL, CONFIG_CREATED)` combinations must now all reach
the same place, and the old expectation is inverted into an explicit "must NOT
happen" so the regression cannot return quietly.

### Issue 3 — ddgs

Two defects are **proven**, four are **mitigations for a symptom that did not
reproduce**. Stated separately rather than blended:

**Proven.** `_ddgs_installed()` was `find_spec(...) is not None`, which stays `True`
for a distribution whose Python files landed but whose native extension did not —
`/search doctor` printed *"ddgs importable: yes"* while every search died. And the
rate-limit test was `"202" in message`, which fires on any year or byte count
(`ValueError("parse failed at 2024 (2029 bytes)")` was reported to users as
throttling, with throttling advice). Also: `DDGS()` was constructed per search and
never closed, leaking an HTTP client per call.

**Not reproduced.** The report is that the second search throttles. Six rapid
distinct queries through the **old** call shape all returned results from this IP, so
engine rotation, retry/backoff, inter-search spacing and the repeat cache are
unproven mitigations, not demonstrated wins. They are worth having — the user hit it
and it is the top recurring issue on the ddgs tracker — but the commit says so.

One claim was corrected rather than shipped: the code first asserted that `auto`
prioritises Wikipedia/Grokipedia and that reordering fixes retrieval. Upstream
documents that, but measured against 9.14.4 `auto` was less encyclopaedic than
described, and the explicit order returned Wikipedia first for the same news-shaped
query anyway. The comment now says the ordering makes intent explicit and stable, not
that it is a measured improvement.

**Live end-to-end**: three distinct queries succeeded (4.8s / 9.7s / 6.2s), verbatim
repeat served from cache in **0.001s**.

**The host map is derived, not guessed** — see
[ddgs-engine-hosts.md](ddgs-engine-hosts.md). It caught three things a guess gets
wrong: `google`/`bing`/`yandex` are **not** registered text engines in ddgs 9.x
(`engines/google.py` exists but is not wired into `ENGINES["text"]`); `yahoo` also
reaches `www.bing.com`; and the pre-existing `lite.duckduckgo.com` entry is stale.
`region` is pinned to `us-en` because Wikipedia's `search_url` is templated
`{lang}.wikipedia.org` — without the pin a non-en region reaches a host the allowlist
never checked.

## Beyond the three issues

Four portability breakages, all now guarded, plus one security-relevant design
change:

- **`curl … | sh`** silently broke on ~30 bash-array sites. Now re-execs under bash
  when the file is on disk, otherwise prints the `| bash` form; with no bash at all it
  says `apk add bash`. Verified: dash and zsh refuse correctly, macOS `/bin/sh` (which
  *is* bash) proceeds correctly, `sh <file>` re-execs.
- **bash 3.2 floor**, verified on this machine's actual 3.2.57, and locked in by
  `scripts/check_installer_portability.sh` (clean on the real file, catches all five
  injected violations). One correction from writing it: `sed -i.bak` **is** portable
  and is used here; only bare `sed -i` is GNU-only.
- **No proxy support at all** (`grep -c proxy` → `0, 0`). Now normalised in both
  directions, with credentials masked before printing — `HTTPS_PROXY` commonly carries
  `user:password@host`, and a test asserts the password never appears in output.
- **Windows MAX_PATH**: detects `LongPathsEnabled` and passes
  `--install-strategy=hoisted` when it is off.
- **The egress check is now per-engine.** The old check refused the *whole* backend if
  any one of three hosts was blocked, so widening the engine list under that rule
  would have made ddgs *more* likely to be denied — the opposite of the intent. It
  still fails closed: an engine is offered only when **every** host it reaches is
  permitted. `yahoo` is the case that matters, since blocking either of its two hosts
  must drop it.
- **CRLF guard** — and the measurement behind it. Under CRLF, bash still executes
  simple commands (the stray CR lands inside an argument) but fails to parse **any**
  compound keyword: `case … in` and both forms of `if … then … fi` die first. A guard
  written with either can never fire on the file it exists to diagnose, which is
  exactly what the first attempt did. It is now a single pipeline-and-list of simple
  commands, placed before everything else, with a comment saying not to tidy it into
  an `if`.

## Explicitly out of scope

`Ensure-SupportedTerminal` (`d296ab5`) is untouched, by decision. It `exit 1`s unless
the host is Windows Terminal ≥1.19 or VS Code, so **that gate — not this branch —
decides which Windows hosts can install**: legacy conhost, Windows Server, Windows 10
pre-1903, machines without winget, and every non-interactive run are refused before
any of this work runs. "Universal on Windows" therefore means universal *within
modern-terminal hosts*. The original brief asked for legacy PowerShell support; that
is not reachable while the gate stands, and nothing here claims otherwise.

Two consequences were still in scope because the gate depends on them, and both are
ordered ahead of it: TLS 1.2 (its relaunch path re-downloads the installer over HTTPS,
which a 5.1 host defaulting to TLS 1.0 cannot do) and the PowerShell 5.1 floor (it
calls `Get-AppxPackage`, which fails cryptically on PS 3.0/4.0).

## Verification summary

```
bash -n install.sh                                     OK
PowerShell parser on install.ps1                       OK
scripts/check_installer_portability.sh                 clean
pytest tests/                                          90 passed, 2 skipped
container install ×2 (ubuntu:24.04, development)        exit 0, exit 0
live ddgs search ×3 distinct + 1 repeat                all served
```

The 2 skips are deliberate: the 5.1 manual-argv-quoting branch and the TLS 1.0
default cannot be exercised on pwsh 7, so they are `skipif`-ed rather than allowed to
pass vacuously. A vacuous pass reads as coverage that does not exist.

## Still needs real Windows

Everything PowerShell-side was verified on **pwsh 7.6.5** (installed via
`brew install powershell` — now a formula, not a cask; it took the repo's installer
tests from *1 passed / 9 skipped* to *15 passed*). Untested until someone runs it on
Windows:

1. The **5.1 manual argv-quoting branch** — 5.1's .NET Framework `ProcessStartInfo`
   has no `ArgumentList`, so the command line is built by hand. Run as a user whose
   `%USERPROFILE%` contains a space.
2. **TLS 1.2 on a genuinely old host** — Server 2016 or Windows 10 pre-1809. On
   current Windows 10/11, 5.1 already defaults to TLS 1.2 and the fix is
   unobservable, so testing there proves nothing. Note the terminal gate blocks the
   end-to-end path on those machines, so verify the block in isolation.
3. **MAX_PATH** — with `LongPathsEnabled = 0`, confirm the bridge installs or warns
   precisely; set it to 1 and confirm the warning disappears.
4. **The stuck-package headline case**, elevated:
   `New-NetFirewallRule -DisplayName a8088-block -Direction Outbound -RemoteAddress 151.101.0.0/16 -Action Block`
   then install. Expect *"uv pip install timed out after 10m — a package download
   stalled"* and a non-zero exit, not a frozen console. Remove with
   `Remove-NetFirewallRule -DisplayName a8088-block`. Lower `$TCoreInstall` for that
   run — `AGENT8088_TIMEOUT_SCALE` *multiplies*.
5. **Setup prompting on a re-run with a real TTY** — the container proves the gate no
   longer short-circuits, but not that the prompt renders.
6. **Non-ASCII install path** — `$env:AGENT8088_HOME = "$env:LOCALAPPDATA\代理8088"`.
7. **Authenticating proxy** with only `HTTPS_PROXY` set and no IE settings: downloads
   succeed and no credential appears in output.
