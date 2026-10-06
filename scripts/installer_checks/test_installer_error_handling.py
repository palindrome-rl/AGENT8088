"""Installer error handling: refusals, stop messages, diagnosis, stamps.

Same convention as test_installer_privileged_run_mode.py and
test_installer_mem0.py: pull individual functions out of install.sh /
install.ps1 by regex and run them under a bare shell with their dependencies
stubbed. Nothing here runs an installer, touches the real HOME, or reaches the
network -- git, apt-get and sudo are fake executables on a private PATH, and
every path lives under tmp_path.
"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BASH = shutil.which("bash") or "/bin/bash"

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX shell")

STUB_LOGS = (
    'log_info() { echo "INFO:$1"; }\n'
    'log_warn() { echo "WARN:$1"; }\n'
    'log_error() { echo "ERR:$1"; }\n'
    'log_success() { echo "OK:$1"; }\n'
    'INSTALL_CMD="curl -fsSL https://raw.githubusercontent.com/palindrome-rl/AGENT8088/AGENT8088-v1.2/install.sh | bash"\n'
)


def _sh_source() -> str:
    return (ROOT / "install.sh").read_text(encoding="utf-8")


def _sh_function(name: str) -> str:
    match = re.search(rf"(?ms)^{re.escape(name)}\(\) \{{.*?^\}}$", _sh_source())
    assert match, f"shell function not found in install.sh: {name}"
    return match.group(0)


def _default_signals():
    # A non-interactive bash cannot trap a signal that was ignored when it
    # started. pytest launched as a background job (or under a harness that
    # ignores SIGINT) passes SIG_IGN down, and the installer's INT/TERM traps
    # then silently never fire -- the interrupt test failed every time there
    # while passing in a terminal. Start the shell the way a terminal would.
    import signal
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)


def _run_sh(tmp_path: Path, body: str, *functions: str, env: dict | None = None):
    script = tmp_path / "harness.sh"
    script.write_text(
        STUB_LOGS + "\n".join(_sh_function(f) for f in functions) + "\n" + body + "\n",
        encoding="utf-8",
    )
    run_env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(tmp_path)}
    run_env.update(env or {})
    return subprocess.run([BASH, str(script)], capture_output=True, text=True,
                          env=run_env, timeout=60,
                          preexec_fn=_default_signals if os.name == "posix" else None)


def _fake_bin(tmp_path: Path, name: str, body: str) -> Path:
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir(exist_ok=True)
    exe = bin_dir / name
    exe.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    exe.chmod(0o755)
    return bin_dir


# ---------------------------------------------------------------------------
# sudo / root
# ---------------------------------------------------------------------------
@posix_only
def test_sudo_invocation_is_refused_with_the_rerun_command(tmp_path):
    result = _run_sh(
        tmp_path,
        'id() { echo 0; }\nAGENT8088_HOME=/root/.agent8088\n'
        'check_invoking_user\necho REACHED',
        "check_invoking_user",
        env={"SUDO_USER": "alice"},
    )
    assert result.returncode == 1
    assert "Don't run the installer with sudo" in result.stdout
    assert "palindrome-rl/AGENT8088/AGENT8088-v1.2/install.sh | bash" in result.stdout
    assert "REACHED" not in result.stdout


@posix_only
def test_plain_root_without_sudo_user_is_allowed_with_a_notice(tmp_path):
    result = _run_sh(
        tmp_path,
        'id() { echo 0; }\nAGENT8088_HOME=/root/.agent8088\n'
        'unset SUDO_USER\ncheck_invoking_user\necho REACHED',
        "check_invoking_user",
    )
    assert result.returncode == 0
    assert "Running as root" in result.stdout
    assert "REACHED" in result.stdout


@posix_only
def test_non_root_user_passes_silently(tmp_path):
    result = _run_sh(tmp_path, 'id() { echo 501; }\ncheck_invoking_user\necho REACHED',
                     "check_invoking_user", env={"SUDO_USER": "alice"})
    assert result.returncode == 0
    assert result.stdout.strip() == "REACHED"


def test_sudo_check_runs_before_anything_writes_into_home():
    main = _sh_function("main")
    assert main.index("check_invoking_user") < main.index("acquire_install_lock")
    assert main.index("acquire_install_lock") < main.index("detect_os")


# ---------------------------------------------------------------------------
# Option parsing
# ---------------------------------------------------------------------------
@posix_only
@pytest.mark.parametrize("value", ["", "--skip-setup"])
def test_branch_without_a_value_is_a_clear_error(tmp_path, value):
    result = _run_sh(tmp_path, f'_require_option_value --branch "{value}" development\necho REACHED',
                     "_require_option_value")
    assert result.returncode == 1
    assert "--branch needs a value" in result.stderr
    assert "REACHED" not in result.stdout


@posix_only
def test_branch_with_a_value_passes(tmp_path):
    result = _run_sh(tmp_path, '_require_option_value --branch dev development\necho REACHED',
                     "_require_option_value")
    assert result.returncode == 0
    assert "REACHED" in result.stdout


def test_branch_and_memory_options_validate_their_value():
    source = _sh_source()
    assert '_require_option_value "$1" "${2:-}" "AGENT8088-v1.2"' in source
    assert '_require_option_value "$1" "${2:-}" "native"' in source


# ---------------------------------------------------------------------------
# Stage tracking + ERR/EXIT/INT traps
# ---------------------------------------------------------------------------
TRAP_FUNCS = ("_enter_stage", "_report_install_stop", "_on_install_exit",
              "_on_install_signal", "install_traps")
TRAP_PRE = ('set -e\nrelease_install_lock() { :; }\nINSTALL_LOG=/tmp/agent8088-test.log\n'
            '_STEP_LOGS=()\n_RWT_CHILD_PID=""\n_INSTALL_STOP_REPORTED=false\nSTAGE=""\n')


@posix_only
def test_failure_inside_a_stage_names_the_stage_line_and_rerun_command(tmp_path):
    result = _run_sh(
        tmp_path,
        TRAP_PRE + 'install_traps\n_enter_stage "repository download"\n'
        'f() {\n  false\n  echo AFTER\n}\nf',
        *TRAP_FUNCS,
    )
    assert result.returncode == 1, "the original exit status must be preserved"
    assert "Install stopped during repository download (line" in result.stdout
    assert "Re-run the same command to resume" in result.stdout
    assert "Log: /tmp/agent8088-test.log" in result.stdout
    assert "AFTER" not in result.stdout


@posix_only
def test_tolerated_failures_do_not_trigger_the_stop_report(tmp_path):
    result = _run_sh(
        tmp_path,
        TRAP_PRE + 'install_traps\n_enter_stage "x"\n'
        'false || true\nx="$(false; echo sub)"\nif false; then :; fi\n'
        'g() { [ 1 = 2 ] && echo no; return 0; }\ng\nrc=0; false || rc=$?\necho CLEAN',
        *TRAP_FUNCS,
    )
    assert result.returncode == 0
    assert "CLEAN" in result.stdout
    assert "Install stopped" not in result.stdout


@posix_only
def test_interrupt_reports_the_stage_and_exits_130(tmp_path):
    result = _run_sh(
        tmp_path,
        TRAP_PRE + 'install_traps\n_enter_stage "Web UI build"\nkill -INT $$\nsleep 1\necho NOT_REACHED',
        *TRAP_FUNCS,
    )
    assert result.returncode == 130
    assert "Install interrupted during Web UI build" in result.stdout
    assert "NOT_REACHED" not in result.stdout


def test_main_sets_a_stage_for_every_install_step():
    main = _sh_function("main")
    for step in ("detect_os", "install_uv", "check_python", "check_git", "clone_repo",
                 "install_deps", "install_node_bridge", "install_webui",
                 "install_code_review", "install_embedding_model",
                 "install_native_sandbox", "setup_path", "drop_config",
                 "run_initial_setup", "verify_install"):
        line = next(l for l in main.splitlines() if re.search(rf"\b{step}\b", l))
        preceding = main[:main.index(line) + len(line)]
        assert "_enter_stage" in preceding.splitlines()[-1] or "_enter_stage" in \
            preceding.splitlines()[-2], f"{step} runs without a stage name"
    assert "install_traps" in main
    # Ctrl-C in the wizard/agent is the user leaving, not an interrupted install.
    assert main.index("trap - INT TERM") < main.index("run_initial_setup")


# ---------------------------------------------------------------------------
# Single-instance lock
# ---------------------------------------------------------------------------
@posix_only
def test_second_installer_is_refused_while_the_first_holds_the_lock(tmp_path):
    home = tmp_path / "agent-home"
    result = _run_sh(
        tmp_path,
        f'AGENT8088_HOME="{home}"\nINSTALL_LOCK_DIR="{home}/.install.lock"\n'
        'acquire_install_lock\necho "HELD=$INSTALL_LOCK_HELD"\n'
        # A second installer in the same state: the pid on file is alive ($$).
        'INSTALL_LOCK_HELD=false\nacquire_install_lock\necho REACHED',
        "acquire_install_lock",
    )
    assert "HELD=true" in result.stdout
    assert result.returncode == 1
    assert "Another agent8088 installer is already running" in result.stdout
    assert "REACHED" not in result.stdout


# ---------------------------------------------------------------------------
# Private repository probe
# ---------------------------------------------------------------------------
FAKE_GIT = """case "$FAKE_GIT_MODE" in
  ok) printf 'abc\\trefs/heads/dev\\n' ;;
  nobranch) printf 'abc\\trefs/heads/development\\n' ;;
  auth) echo "fatal: could not read Username for 'https://github.com': terminal prompts disabled" >&2; exit 128 ;;
  access) echo "remote: Repository not found." >&2; exit 128 ;;
  net) echo "fatal: unable to access 'x': Could not resolve host: github.com" >&2; exit 128 ;;
esac
"""
PROBE_FUNCS = ("probe_repo_access", "_check_branch_listed", "classify_git_error",
               "_print_repo_auth_help", "run_logged", "show_step_failure",
               "diagnose_install_output", "_install_cmd_with")
PROBE_PRE = ('REPO_URL=https://github.com/o/r.git\nBRANCH=dev\nINSTALL_LOG=/dev/null\n'
             '_STEP_LOGS=()\nRUN_LOGGED_FOREGROUND=false\nAGENT8088_HOME=/x\nINSTALL_DIR=/x/a\n'
             '_can_prompt() { return 1; }\nrun_with_timeout() { shift; "$@"; }\n')


def _probe(tmp_path, mode):
    bin_dir = _fake_bin(tmp_path, "git", FAKE_GIT)
    return _run_sh(tmp_path, PROBE_PRE + 'probe_repo_access\necho REACHED', *PROBE_FUNCS,
                   env={"PATH": f"{bin_dir}:/usr/bin:/bin", "FAKE_GIT_MODE": mode})


@posix_only
def test_probe_passes_when_the_branch_exists(tmp_path):
    result = _probe(tmp_path, "ok")
    assert result.returncode == 0
    assert "REACHED" in result.stdout


@posix_only
def test_probe_names_a_missing_branch(tmp_path):
    result = _probe(tmp_path, "nobranch")
    assert result.returncode == 1
    assert "Branch 'dev' does not exist" in result.stdout


@posix_only
def test_probe_auth_failure_needs_no_token_and_shows_git_stderr(tmp_path):
    result = _probe(tmp_path, "auth")
    assert result.returncode == 1
    assert "public and needs no GitHub token" in result.stdout
    assert "stale github.com credentials" in result.stdout
    assert "personal access token" not in result.stdout
    assert "terminal prompts disabled" in result.stdout  # git's own stderr is shown


@posix_only
def test_probe_access_denied_is_not_reported_as_a_network_problem(tmp_path):
    result = _probe(tmp_path, "access")
    assert result.returncode == 1
    assert "could not find the public repository" in result.stdout
    assert "network" not in result.stdout.lower()


@posix_only
@pytest.mark.parametrize("text,kind", [
    ("fatal: Authentication failed for 'https://github.com/x'", "auth"),
    ("remote: Repository not found.", "access"),
    ("fatal: unable to access 'x': SSL certificate problem: unable to get local issuer certificate", "tls"),
    ("fatal: unable to access 'x': Could not resolve host: github.com", "network"),
    ("something else", "unknown"),
])
def test_classify_git_error(tmp_path, text, kind):
    result = _run_sh(tmp_path, f'classify_git_error "{text}"', "classify_git_error")
    assert result.stdout.strip() == kind


def test_clone_and_fetch_probe_first_and_keep_git_stderr():
    clone = _sh_function("clone_repo")
    assert clone.count("probe_repo_access") == 2
    assert "git fetch --depth 1 origin" in clone and "show_step_failure" in clone
    assert 'git fetch --depth 1 origin "$BRANCH" >/dev/null 2>&1' not in clone


# ---------------------------------------------------------------------------
# Failure diagnosis
# ---------------------------------------------------------------------------
@posix_only
@pytest.mark.parametrize("line,expected", [
    ("error: invalid peer certificate: UnknownIssuer", "UV_NATIVE_TLS=1"),
    ("OSError: [Errno 28] No space left on device", "disk is full"),
    ("No solution found when resolving dependencies", "no build for this Python"),
    ("error sending request for url (https://pypi.org/simple/x)", "HTTPS_PROXY"),
])
def test_core_install_failures_are_diagnosed(tmp_path, line, expected):
    log = tmp_path / "step.log"
    log.write_text(line + "\n", encoding="utf-8")
    result = _run_sh(tmp_path, f'AGENT8088_HOME=/x\nINSTALL_DIR=/x/a\ndiagnose_install_output "{log}"',
                     "diagnose_install_output", "_install_cmd_with")
    assert expected in result.stdout


def test_core_install_output_goes_to_the_log_not_dev_null():
    deps = _sh_function("install_deps")
    assert "--reinstall-package agent8088" in deps
    assert re.search(r'run_logged "\$T_CORE_INSTALL" "\$UV_CMD" pip install', deps)
    assert "show_step_failure" in deps


# ---------------------------------------------------------------------------
# Python provisioning
# ---------------------------------------------------------------------------
def test_uv_python_install_is_bounded_and_falls_back_to_system_python():
    body = _sh_function("check_python")
    assert re.search(r'run_logged "\$T_VENV" "\$UV_CMD" python install', body)
    assert '"$UV_CMD" python install "$PYTHON_VERSION" >/dev/null 2>&1' not in body
    assert '"3.12"' in body
    assert "sys.version_info >= (3, 10)" in body
    assert "show_step_failure" in body


# ---------------------------------------------------------------------------
# Pre-flight
# ---------------------------------------------------------------------------
@posix_only
def test_low_disk_space_fails_the_preflight(tmp_path):
    result = _run_sh(
        tmp_path,
        'df() { printf "Filesystem 1024-blocks Used Available Capacity Mounted\\n'
        '/dev/x 100 50 1000 50%% /\\n"; }\n'
        f'AGENT8088_HOME="{tmp_path}/h"\nINSTALL_DIR="{tmp_path}/h/agent8088"\n'
        'check_disk_space; echo "RC=$?"',
        "check_disk_space", "_install_cmd_with",
    )
    assert "RC=1" in result.stdout
    assert "needs about 4 GB" in result.stdout


@posix_only
def test_unreadable_df_output_warns_but_passes(tmp_path):
    result = _run_sh(
        tmp_path,
        f'df() {{ echo garbage; }}\nAGENT8088_HOME="{tmp_path}"\nINSTALL_DIR="{tmp_path}/a"\n'
        'check_disk_space; echo "RC=$?"',
        "check_disk_space", "_install_cmd_with",
    )
    assert "RC=0" in result.stdout
    assert "skipping the check" in result.stdout


@posix_only
def test_unreachable_host_is_named(tmp_path):
    bin_dir = _fake_bin(tmp_path, "curl", "exit 6\n")
    result = _run_sh(tmp_path, 'check_connectivity; echo "RC=$?"',
                     "check_connectivity", "_install_cmd_with",
                     env={"PATH": f"{bin_dir}:/usr/bin:/bin"})
    assert "RC=0" in result.stdout, "connectivity is advisory, never fatal"
    assert "Cannot reach https://github.com" in result.stdout
    assert "Cannot reach https://astral.sh" in result.stdout


# ---------------------------------------------------------------------------
# Completion stamps
# ---------------------------------------------------------------------------
@posix_only
def test_stamp_detects_changed_lockfile(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "package-lock.json").write_text('{"a": 1}\n', encoding="utf-8")
    stamp = tmp_path / "stamp"
    result = _run_sh(
        tmp_path,
        f'fp="$(_npm_inputs_fingerprint "{proj}")"\n'
        f'_stamp_matches "{stamp}" "$fp" && echo MATCH_BEFORE_WRITE\n'
        f'_write_stamp "{stamp}" "$fp"\n'
        f'_stamp_matches "{stamp}" "$fp" && echo MATCH_AFTER_WRITE\n'
        f'echo changed >> "{proj}/package-lock.json"\n'
        f'_stamp_matches "{stamp}" "$(_npm_inputs_fingerprint "{proj}")" || echo STALE\n'
        'true',
        "_fingerprint_file", "_stamp_matches", "_write_stamp", "_npm_inputs_fingerprint",
    )
    assert "MATCH_BEFORE_WRITE" not in result.stdout
    assert "MATCH_AFTER_WRITE" in result.stdout
    assert "STALE" in result.stdout


def test_partial_artifacts_are_not_trusted_without_a_stamp():
    bridge = _sh_function("install_node_bridge")
    assert ".agent8088-install-stamp" in bridge and "_write_stamp" in bridge
    web = _sh_function("install_webui")
    assert ".agent8088-build-stamp" in web and "rev-parse HEAD:web" in web
    review = _sh_function("install_code_review")
    assert '_stamp_matches "$_review_stamp" "$OPEN_CODE_REVIEW_VERSION"' in review


# ---------------------------------------------------------------------------
# apt-get
# ---------------------------------------------------------------------------
@posix_only
def test_apt_install_never_runs_a_bare_sudo_without_a_terminal(tmp_path):
    calls = tmp_path / "calls"
    bin_dir = _fake_bin(tmp_path, "sudo", f'echo "sudo $*" >> "{calls}"\n')
    _fake_bin(tmp_path, "apt-get", f'echo "apt-get $*" >> "{calls}"\n')
    result = _run_sh(
        tmp_path,
        'INSTALL_LOG=/dev/null\n_STEP_LOGS=()\nRUN_LOGGED_FOREGROUND=false\nT_PIP=5\n'
        'run_with_timeout() { shift; "$@"; }\n_privileged_run_mode() { echo skip; }\n'
        '_apt_install 5 socat; echo "RC=$?"',
        "_apt_install", "run_logged",
        env={"PATH": f"{bin_dir}:/usr/bin:/bin"},
    )
    assert "RC=1" in result.stdout
    assert "sudo apt-get update" in result.stdout  # the manual fix is printed
    assert not calls.exists(), "nothing may be executed when there is no way to authenticate"


@posix_only
def test_apt_install_refreshes_lists_when_the_install_fails(tmp_path):
    calls = tmp_path / "calls"
    updated = tmp_path / "updated"
    bin_dir = _fake_bin(
        tmp_path, "apt-get",
        f'echo "apt-get $*" >> "{calls}"\n'
        f'case "$*" in *update*) touch "{updated}" ;; *install*) [ -f "{updated}" ] || exit 100 ;; esac\n',
    )
    result = _run_sh(
        tmp_path,
        'INSTALL_LOG=/dev/null\n_STEP_LOGS=()\nRUN_LOGGED_FOREGROUND=false\nT_PIP=5\n'
        'run_with_timeout() { shift; "$@"; }\n_privileged_run_mode() { echo direct; }\n'
        # Pretend the lists exist so only the failure path can trigger an update.
        'ls() { return 0; }\n'
        '_apt_install 5 socat; echo "RC=$?"',
        "_apt_install", "run_logged",
        env={"PATH": f"{bin_dir}:/usr/bin:/bin"},
    )
    assert "RC=0" in result.stdout
    lines = calls.read_text().splitlines()
    assert lines == ["apt-get install -y -qq socat", "apt-get update -qq",
                     "apt-get install -y -qq socat"]


def test_sandbox_helpers_and_libreoffice_use_the_apt_helper():
    sandbox = _sh_function("install_native_sandbox")
    assert '_apt_install "$T_PIP" "${_apt_pkgs[@]}"' in sandbox
    libreoffice = _sh_function("install_libreoffice")
    assert '_apt_install "$T_LIBREOFFICE" libreoffice' in libreoffice


# ---------------------------------------------------------------------------
# verify_install / PATH
# ---------------------------------------------------------------------------
VERIFY_FUNCS = ("get_command_link_dir", "is_termux", "run_logged", "show_step_failure",
                "diagnose_install_output", "check_command_shadowing",
                "print_skipped_summary", "verify_install", "_install_cmd_with",
                "_json_escape", "write_install_state")


def _verify(tmp_path, shim_body, path_dirs=()):
    link = tmp_path / "bin"
    link.mkdir()
    shim = link / "agent8088"
    shim.write_text("#!/bin/sh\n" + shim_body, encoding="utf-8")
    shim.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    pre = ('INSTALL_LOG=/dev/null\n_STEP_LOGS=()\nSKIPPED_STAGES=()\nRUN_LOGGED_FOREGROUND=false\n'
           'INSTALLER_URL=u\nBRANCH=development\nrun_with_timeout() { shift; "$@"; }\n')
    path = ":".join([*map(str, path_dirs), "/usr/bin", "/bin"])
    return _run_sh(
        tmp_path, pre + 'verify_install; echo "RC=$?"', *VERIFY_FUNCS,
        # A SHELL that does not exist skips the login-shell probe, whose
        # /etc/profile would otherwise add whatever the host has on PATH.
        env={"PATH": path, "HOME": str(home), "SHELL": "/nonexistent/sh",
             "AGENT8088_LINK_DIR": str(link),
             "AGENT8088_HOME": str(home / ".agent8088"),
             "INSTALL_DIR": str(home / ".agent8088" / "agent8088")},
    )


@posix_only
def test_broken_shim_is_reported_instead_of_done(tmp_path):
    result = _verify(tmp_path, 'echo "ModuleNotFoundError: No module named agent8088" >&2\nexit 1\n')
    assert "RC=1" in result.stdout
    assert "installed but does not start" in result.stdout
    assert "ModuleNotFoundError" in result.stdout
    assert "Done." not in result.stdout


@posix_only
def test_healthy_shim_prints_done_session_path_and_doctor_hint(tmp_path):
    result = _verify(tmp_path, 'echo "agent8088 1.2.3"\n')
    assert "RC=0" in result.stdout
    assert "agent8088 is ready (agent8088 1.2.3)" in result.stdout
    assert "Done." in result.stdout
    assert 'export PATH="' in result.stdout  # link dir is not on this PATH
    assert "enter /doctor" in result.stdout


@posix_only
def test_shadowing_agent8088_earlier_in_path_is_warned(tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    (other / "agent8088").write_text("#!/bin/sh\necho other\n", encoding="utf-8")
    (other / "agent8088").chmod(0o755)
    result = _verify(tmp_path, 'echo "agent8088 1.2.3"\n', path_dirs=(other,))
    assert f"Another agent8088 at {other}/agent8088 will run instead" in result.stdout


@posix_only
def test_fish_config_is_written_to_conf_d(tmp_path):
    result = _run_sh(
        tmp_path,
        f'AGENT8088_HOME="{tmp_path}/.agent8088"\nFISH_CONF_FILE="{tmp_path}/.config/fish/conf.d/agent8088.fish"\n'
        'write_fish_config /opt/link',
        "write_fish_config",
    )
    assert result.returncode == 0, result.stderr
    text = (tmp_path / ".config/fish/conf.d/agent8088.fish").read_text()
    assert 'set -gx PATH "/opt/link" $PATH' in text
    assert f'set -gx AGENT8088_CONFIG "{tmp_path}/.agent8088/config.txt"' in text


def test_bash_profile_is_not_created_next_to_an_existing_profile():
    """bash reads only the first of ~/.bash_profile, ~/.bash_login, ~/.profile;
    touching a new ~/.bash_profile silently stops ~/.profile being read."""
    body = _sh_function("setup_path")
    assert '"$HOME/.bashrc" "$HOME/.bash_profile" "$HOME/.profile"' not in body
    assert "bash_login=" in body


# ---------------------------------------------------------------------------
# Unknown CPU architecture
# ---------------------------------------------------------------------------
def test_unknown_arch_records_a_skip_instead_of_downloading_x64():
    body = _sh_function("install_node_bridge")
    assert '*)            _arch="x64" ;;' not in body
    assert "no Node.js build for CPU" in body


# ---------------------------------------------------------------------------
# dash / `curl | sh`
# ---------------------------------------------------------------------------
def _top_snippet(tmp_path: Path) -> Path:
    lines = _sh_source().splitlines(keepends=True)
    end = next(i for i, l in enumerate(lines) if l.rstrip("\n") == "esac")
    snippet = tmp_path / "top.sh"
    snippet.write_text("".join(lines[: end + 1]) + "echo REACHED_BASH_BODY\n", encoding="utf-8")
    return snippet


@posix_only
def test_piped_to_dash_prints_the_bash_hint_without_bad_substitution(tmp_path):
    dash = shutil.which("dash")
    if not dash:
        pytest.skip("dash is not installed")
    snippet = _top_snippet(tmp_path)
    with snippet.open("rb") as stdin:
        result = subprocess.run([dash], stdin=stdin, capture_output=True, text=True, timeout=30)
    assert result.returncode == 1
    assert "Bad substitution" not in result.stderr
    assert "This installer needs bash" in result.stderr
    assert "palindrome-rl/AGENT8088/AGENT8088-v1.2/install.sh | bash" in result.stderr
    assert "CRLF" not in result.stderr


@posix_only
def test_crlf_guard_still_fires_under_dash_and_bash(tmp_path):
    snippet = _top_snippet(tmp_path)
    crlf = tmp_path / "crlf.sh"
    crlf.write_bytes(snippet.read_bytes().replace(b"\n", b"\r\n"))
    for shell in filter(None, (shutil.which("dash"), BASH)):
        result = subprocess.run([shell, str(crlf)], capture_output=True, text=True, timeout=30)
        assert "Windows (CRLF) line endings" in result.stderr, shell
        assert result.returncode != 0


def test_no_placeholder_urls_in_user_facing_installer_messages():
    for name in ("install.sh", "install.ps1"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "<YOUR-URL>" not in text, name
        code = [l for l in text.splitlines() if not l.lstrip().startswith("#")]
        assert not any("<url>" in l for l in code), name




# ---------------------------------------------------------------------------
# install.ps1
# ---------------------------------------------------------------------------
def _ps_function(name: str) -> str:
    source = (ROOT / "install.ps1").read_text(encoding="utf-8")
    match = re.search(rf"(?ms)^function {re.escape(name)} \{{.*?^\}}\r?$", source)
    assert match, f"PowerShell function not found: {name}"
    return match.group(0)


def _run_ps(script: str) -> str:
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if not powershell:
        pytest.skip("PowerShell is not installed")
    result = subprocess.run([powershell, "-NoProfile", "-Command", script],
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


PS_STUBS = (
    'function Write-Info { param([string]$Message) Write-Output "INFO:$Message" }\n'
    'function Write-Warn { param([string]$Message) Write-Output "WARN:$Message" }\n'
    'function Write-Err { param([string]$Message) Write-Output "ERR:$Message" }\n'
    '$RepoUrl = "https://github.com/o/r.git"; $Branch = "dev"; $script:InstallLog = $null\n'
)


@pytest.mark.parametrize("probe,expected", [
    ('@{ ExitCode = 0; TimedOut = $false; Output = "abc`trefs/heads/dev`n"; ErrorOutput = "" }', "ok"),
    ('@{ ExitCode = 0; TimedOut = $false; Output = "abc`trefs/heads/main`n"; ErrorOutput = "" }', "branch"),
    ('@{ ExitCode = 128; TimedOut = $false; Output = ""; ErrorOutput = "fatal: Authentication failed" }', "auth"),
    ('@{ ExitCode = 128; TimedOut = $false; Output = ""; ErrorOutput = "remote: Repository not found." }', "access"),
    ('@{ ExitCode = -1; TimedOut = $true; Output = ""; ErrorOutput = "" }', "network"),
])
def test_ps_repo_probe_classifies(probe, expected):
    out = _run_ps(
        PS_STUBS
        + f'function Invoke-WithTimeout {{ param($FilePath, $Arguments, $TimeoutSec, [switch]$CaptureOutput, $Activity) {probe} }}\n'
        + "\n".join(_ps_function(f) for f in ("Get-GitFailureKind", "Add-InstallLog", "Test-RepoAccess"))
        + '\n$env:GIT_TERMINAL_PROMPT = "orig"'
        + '\nWrite-Output "KIND=$(Test-RepoAccess)"\nWrite-Output "PROMPT=[$env:GIT_TERMINAL_PROMPT]"'
    )
    assert f"KIND={expected}" in out
    assert "PROMPT=[orig]" in out, "the probe must restore GIT_TERMINAL_PROMPT"


def test_ps_clone_skips_the_zip_fallback_for_a_private_repo_auth_failure():
    clone = _ps_function("Clone-Repo")
    assert "Test-RepoAccess" in clone
    auth_branch = clone.index("if ($authProblem -or $cloneKind -in @(\"auth\", \"access\"))")
    assert auth_branch < clone.index("falling back to ZIP archive")
    assert "2>$null" not in clone.split("fetch --depth 1 origin $Branch", 1)[1].split("\n", 1)[0]


def test_ps_core_install_captures_output_for_the_failure_message():
    deps = _ps_function("Install-Deps")
    assert "-CaptureOutput" in deps
    assert "Write-OutputTail" in deps and "Write-InstallFailureHint" in deps


def test_ps_tls_failure_is_diagnosed():
    out = _run_ps(PS_STUBS + _ps_function("Write-InstallFailureHint")
                  + '\nWrite-InstallFailureHint -Text "error: invalid peer certificate: UnknownIssuer"')
    assert "UV_NATIVE_TLS" in out


def test_ps_verify_install_checks_the_exit_code_and_main_reports_failure():
    verify = _ps_function("Verify-Install")
    assert '@("--version")' in verify
    assert "$script:InstallBroken = $true" in verify
    assert "Test-CommandShadowing" in verify
    assert "enter /doctor" in verify
    source = (ROOT / "install.ps1").read_text(encoding="utf-8")
    main = source[source.rfind("try {"):]
    assert main.index("Verify-Install") < main.index("if ($script:InstallBroken)") < main.index("Start-InitialAgent")


def test_ps_warns_when_running_elevated():
    source = (ROOT / "install.ps1").read_text(encoding="utf-8")
    assert "Show-AdministratorWarningIfNeeded" in source
    warn = _ps_function("Show-AdministratorWarningIfNeeded")
    assert "non-admin" in warn


def test_ps_uv_copies_instead_of_hardlinking_but_keeps_a_user_choice():
    """uv hardlinks package files out of one shared cache. Once a cached file is
    also linked into a venv inside a OneDrive-synced folder, Windows refuses any
    further hardlink to it ("The cloud operation cannot be performed on a file
    with incompatible hardlinks", os error 396), and every new install on that
    machine failed at `uv pip install`."""
    source = (ROOT / "install.ps1").read_text(encoding="utf-8")
    line = 'if (-not $env:UV_LINK_MODE) { $env:UV_LINK_MODE = "copy" }'
    assert line in source
    assert source.index(line) < source.index("function Install-Uv")  # before any uv runs
