"""install.sh's curl-or-wget download fallback, exercised in isolation.

Mirrors the extraction convention in test_installer_timeouts.py: pull just
run_with_timeout + _download_file out of install.sh and run them under a bare bash,
with `curl`/`wget` shadowed by a PATH-first stub so the test never touches the
network.

Two mechanics need to be pinned down carefully, since a naive harness looks right
but silently exercises the real system tools instead of the stubs:

  * `_download_file` redirects the curl/wget call's stderr to /dev/null (so a
    stalled/failed download doesn't spam the installer's own output). That means a
    stub cannot prove it ran by writing to stderr -- the caller throws it away
    before the test ever sees it. Stubs below signal via stdout instead.
  * A plain `PATH=fake_bin:$PATH` only shadows a tool if fake_bin actually contains
    a same-named file; when it doesn't (e.g. the "curl absent" case), *both*
    `command -v curl` AND bash's own exec of `curl` as a plain command fall
    straight through to the real PATH -- so a regression from "curl absent -> use
    wget" to "curl fails -> use wget" would still make the test pass, just via a
    real (network-dependent) curl invocation instead of the intended absence
    check. Fix: PATH is set to fake_bin ONLY (not prepended to the real PATH), so
    there is no real curl/wget left to fall through to; only stubs that are
    actually written to fake_bin can be found, by `command -v` or by direct exec.
    `bash` itself is invoked via an absolute path resolved before PATH is
    restricted, so the harness doesn't need the real PATH to find the shell.

Note on what "curl absent" can and can't prove: once PATH holds only fake_bin,
attempting to exec a genuinely-missing `curl` fails with the shell's own
"command not found" (exit 127) -- and that is indistinguishable, from the
outside, from a would-be curl invocation returning any other nonzero status.
So *no* black-box test built around "curl is absent" can, by itself, tell an
absence-gated implementation (checks `command -v curl` first, only calls curl
if present) apart from a failure-gated one (always execs curl, treats any
nonzero -- including "not found" -- as "try wget next"): both fall back to
wget and succeed, identically. The two implementations only diverge when curl
IS present and its invocation fails on its own terms (bad host, refused
connection, etc.) -- there, the correct/absence-gated code must return curl's
own failure rather than additionally trying wget. That is what
`test_does_not_fall_back_when_curl_fails` below exercises; it is the test that
actually catches an absence-gated -> failure-gated regression.
"""
import os
import shutil
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash") or "/bin/bash"


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
    # PATH holds fake_bin ONLY -- not prepended to the real PATH. If it were
    # prepended, a stub-less tool (e.g. no curl stub, to simulate "curl absent")
    # would still resolve to the host's real curl once bash searches past
    # fake_bin, both for `command -v curl` and for a direct `curl ...` exec. That
    # would make a regressed implementation -- one that execs curl unconditionally
    # and falls back to wget only when curl *fails* -- pass this harness's
    # "absent" tests too (the real curl would fail on a bogus URL and the code
    # would still fall back), even though it no longer checks absence at all.
    # With no real PATH left, an unstubbed tool is truly unreachable, so presence
    # vs. absence is what actually gates the fallback, not presence vs. failure.
    env["PATH"] = str(fake_bin)
    script = (
        'CURL_STALL_FLAGS=(--connect-timeout 20)\n'
        'run_with_timeout() { local secs="$1"; shift; "$@"; }\n'
        'log_warn() { echo "$1"; }\n'
        + _extract("_download_file")
        + "\n"
        + extra_script
    )
    return subprocess.run([BASH, "-c", script], env=env,
                           capture_output=True, text=True, timeout=10)


def test_uses_curl_when_present(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_stub(fake_bin, "curl", 'echo "curl called: $*"; exit 0')
    result = _run(fake_bin, '_download_file "http://x" "/tmp/out" 5; echo "rc=$?"')
    assert "curl called" in result.stdout
    assert "rc=0" in result.stdout


def test_falls_back_to_wget_when_curl_absent(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_stub(fake_bin, "wget", 'echo "wget called: $*"; exit 0')
    # fake_bin has no curl, and _run's PATH is fake_bin only (no real PATH
    # behind it), so there is no real curl left for bash to fall through to --
    # curl reads as genuinely absent, not merely failing.
    result = _run(fake_bin, '_download_file "http://x" "/tmp/out" 5; echo "rc=$?"')
    assert "wget called" in result.stdout
    assert "rc=0" in result.stdout


def test_does_not_fall_back_when_curl_fails(tmp_path):
    # curl is PRESENT but fails on its own terms (e.g. host unreachable,
    # connection refused) -- this is the case that actually distinguishes
    # absence-gated fallback from failure-gated fallback (see module
    # docstring). wget is also present and would happily "succeed" if called,
    # so if it gets invoked at all, that proves the implementation regressed
    # to falling back on curl *failure* rather than curl *absence*.
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_stub(fake_bin, "curl", 'echo "curl called: $*"; exit 6')
    _write_stub(fake_bin, "wget", 'echo "wget called: $*"; exit 0')
    result = _run(fake_bin, '_download_file "http://x" "/tmp/out" 5; echo "rc=$?"')
    assert "curl called" in result.stdout
    assert "wget called" not in result.stdout
    assert "rc=6" in result.stdout


def test_warns_when_neither_tool_exists(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    result = _run(fake_bin, '_download_file "http://x" "/tmp/out" 5 2>&1; echo "rc=$?"')
    assert "Neither curl nor wget" in result.stdout
    assert "rc=1" in result.stdout
