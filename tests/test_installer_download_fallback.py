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
    a same-named file; the "curl absent" and "neither present" cases otherwise fall
    straight through to whatever curl/wget the host machine has installed (which,
    on most dev machines and CI images, is both). `command -v curl`/`wget` is
    overridden below to consult fake_bin only, independent of the real PATH, so
    presence/absence is deterministic regardless of what the host has installed.
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
    # `command -v curl`/`wget` is overridden to check fake_bin only, so the
    # curl-present / wget-only / neither-present scenarios are deterministic no
    # matter what the host machine actually has installed.
    command_override = (
        'command() {\n'
        '    if [ "$1" = "-v" ] && { [ "$2" = "curl" ] || [ "$2" = "wget" ]; }; then\n'
        f'        if [ -x "{fake_bin}/$2" ]; then echo "{fake_bin}/$2"; return 0; '
        'else return 1; fi\n'
        '    fi\n'
        '    builtin command "$@"\n'
        '}\n'
    )
    script = (
        'CURL_STALL_FLAGS=(--connect-timeout 20)\n'
        'run_with_timeout() { local secs="$1"; shift; "$@"; }\n'
        'log_warn() { echo "$1"; }\n'
        + command_override
        + _extract("_download_file")
        + "\n"
        + extra_script
    )
    return subprocess.run(["bash", "-c", script], env=env,
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
    # fake_bin has no curl, and the harness's `command -v` override consults only
    # fake_bin, so curl reads as absent regardless of what the host has installed.
    result = _run(fake_bin, '_download_file "http://x" "/tmp/out" 5; echo "rc=$?"')
    assert "wget called" in result.stdout
    assert "rc=0" in result.stdout


def test_warns_when_neither_tool_exists(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    result = _run(fake_bin, '_download_file "http://x" "/tmp/out" 5 2>&1; echo "rc=$?"')
    assert "Neither curl nor wget" in result.stdout
    assert "rc=1" in result.stdout
