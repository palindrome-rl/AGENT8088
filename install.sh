#!/bin/bash
# ============================================================================
# Agent8088 Installer — Linux, macOS, WSL2, Termux
# ============================================================================
# Usage:
#   curl -fsSL --proto '=https' --tlsv1.2 https://<YOUR-URL>/install.sh | bash
#
# Installs agent8088 as an isolated uv tool with a global `agent8088` command.
# Handles: uv bootstrap, Python provisioning, git install, repo clone, venv,
# editable install, PATH/shim, config drop, and a setup wizard.
# ============================================================================

# ----------------------------------------------------------------------------
# CRLF guard -- must be the very first executable code, and must stay one line
# ----------------------------------------------------------------------------
# A checkout made on Windows with core.autocrlf=true and then run under WSL or Git
# Bash gives every line a trailing CR. bash reports that as `$'\r': command not
# found`, or a syntax error, on a line that looks perfectly fine -- which sends
# people hunting the wrong bug entirely.
#
# This is written as a single pipeline-and-list of simple commands, on purpose and
# tested: under CRLF, bash still executes simple commands (the stray CR just ends
# up inside an argument) but fails to parse ANY compound keyword. `case ... in` and
# both forms of `if ... then ... fi` die with a syntax error before running, so a
# guard written with either can never fire on the file it is meant to diagnose.
# Keep this on one line, before everything else, and do not "tidy" it into an if.
#
# .gitattributes (install.sh text eol=lf) is the actual prevention; this is the
# diagnostic for a checkout that predates it. `exit 1` picks up the stray CR and
# so exits 255 with a "numeric argument required" note -- cosmetic, still non-zero.
head -n 1 "${BASH_SOURCE[0]:-/dev/null}" 2>/dev/null | grep -q "$(printf '\r')" && printf '%s\n' "ERROR: this file has Windows (CRLF) line endings." "  Fix:  perl -pi -e 's/\r\$//' \"${BASH_SOURCE[0]}\"" "  Or:   curl -fsSL <url> | bash   (always LF)" >&2 && exit 1

# ----------------------------------------------------------------------------
# Shell preflight -- must be the first executable code in this file
# ----------------------------------------------------------------------------
# This script uses bash arrays in ~30 places. Under dash, busybox ash or zsh those
# are either a syntax error or silently wrong (zsh arrays are 1-indexed), and the
# failure surfaces hundreds of lines later as something apparently unrelated. The
# documented invocation is `curl -fsSL <url> | bash`, but `| sh` is the reflex, so
# re-exec under bash rather than refusing -- and only refuse when there is no bash
# at all.
#
# Written in strict POSIX sh, because it has to parse before we know what shell is
# running it. Nothing above this point may use a bash-only construct.
if [ -z "${BASH_VERSION:-}" ]; then
    if command -v bash >/dev/null 2>&1; then
        # When piped, $0 is "sh"/"bash" rather than a path, so re-execing $0 cannot
        # work and there is no file to hand to bash either -- print the fix instead.
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

# bash 3.2 is the floor: stock macOS ships 3.2.57 and will never be upgraded, so
# this script is written to that level (no associative arrays, no ${x,,}, no
# mapfile). scripts/check_installer_portability.sh keeps it that way. Anything
# older than 3.1 predates `+=(` on arrays.
case "${BASH_VERSINFO[0]:-0}" in
    0|1|2) echo "ERROR: bash ${BASH_VERSION:-?} is too old; bash 3.2+ required." >&2; exit 1 ;;
esac

set -e

# Guard against environment leakage when launched from another tool session.
if [ -n "${PYTHONPATH:-}" ]; then
    echo "⚠ Ignoring inherited PYTHONPATH during install to avoid module shadowing"
    unset PYTHONPATH
fi
if [ -n "${PYTHONHOME:-}" ]; then
    echo "⚠ Ignoring inherited PYTHONHOME during install"
    unset PYTHONHOME
fi

# Prevent uv from discovering config files from the wrong user's home dir.
export UV_NO_CONFIG=1

# ----------------------------------------------------------------------------
# Tool-level stall guards
# ----------------------------------------------------------------------------
# The wall-clock wrappers below are a backstop, not the first line of defence.
# Without these, a registry that accepts the connection and then goes quiet burns
# the ENTIRE wall-clock budget inside one dead request, so the wrapper's kill is
# the first thing that happens rather than a fast failure and a retry against a
# mirror that works. Each tool has its own stall detector; turn them all on.
#
# Only set when unset, so an operator on a genuinely slow link can raise them.
export UV_HTTP_TIMEOUT="${UV_HTTP_TIMEOUT:-60}"
export PIP_DEFAULT_TIMEOUT="${PIP_DEFAULT_TIMEOUT:-60}"
export PIP_RETRIES="${PIP_RETRIES:-3}"

# git aborts a transfer that stays under 1 KB/s for 60s. This is what bounds the
# byte-moving part of `git clone` / `git fetch`; run_with_timeout around them
# bounds the local object-write phase, which these two variables do not cover.
export GIT_HTTP_LOW_SPEED_LIMIT="${GIT_HTTP_LOW_SPEED_LIMIT:-1000}"
export GIT_HTTP_LOW_SPEED_TIME="${GIT_HTTP_LOW_SPEED_TIME:-60}"

# Curl flags for every download: give up on a dead TCP handshake, and abort a
# transfer crawling under 1 KB/s for 30s. An array, not a space-separated string:
# an unquoted string only word-splits in shells that do that, so the string form
# silently degrades to one giant argument ("option --connect-timeout 20 ...: is
# unknown") anywhere it is reused outside bash. The array is also SC2086-clean.
CURL_STALL_FLAGS=(--connect-timeout 20 --speed-limit 1024 --speed-time 30)

# Download one URL to one path, trying curl first and falling back to wget. Some
# minimal base images (notably a few Alpine variants used in CI containers) ship
# wget but not curl, or vice versa -- Aider's installer documents the same
# curl-then-wget fallback for exactly this reason. Returns the underlying tool's
# exit code; the caller already treats a non-zero/timeout result as "this optional
# stage didn't work" rather than a hard failure.
_download_file() {
    local _dl_url="$1" _dl_out="$2" _dl_timeout="$3"
    if command -v curl >/dev/null 2>&1; then
        # --proto '=https' --tlsv1.2: refuse anything but https + TLS1.2+, so a
        # compromised/misconfigured DNS or redirect can't quietly downgrade this
        # to a plaintext or legacy-TLS transfer. This matters here specifically
        # because the one call site extracts and then EXECUTES the downloaded
        # archive (a Node.js runtime, then npm installs from it) with no
        # checksum verification of its own.
        run_with_timeout "$_dl_timeout" curl -fsSL --proto '=https' --tlsv1.2 \
            "${CURL_STALL_FLAGS[@]}" "$_dl_url" -o "$_dl_out" 2>/dev/null
        return $?
    fi
    if command -v wget >/dev/null 2>&1; then
        # wget's nearest equivalents: --timeout bounds a stalled read (curl's
        # --speed-time), --tries=1 avoids wget's own silent retry loop stacking on
        # top of run_with_timeout's wall clock. --https-only is wget's closest
        # match to curl's --proto/--tlsv1.2 pin above: it refuses a plain-http
        # URL or a redirect down to one.
        run_with_timeout "$_dl_timeout" wget -q --https-only --timeout=30 --tries=1 -O "$_dl_out" "$_dl_url" 2>/dev/null
        return $?
    fi
    log_warn "Neither curl nor wget is available - cannot download $_dl_url"
    return 1
}

# ----------------------------------------------------------------------------
# Proxy
# ----------------------------------------------------------------------------
# curl, uv, pip and git all read HTTP_PROXY / HTTPS_PROXY / NO_PROXY already, so
# the work here is normalisation rather than plumbing: curl prefers the lowercase
# spelling and Python's requests prefers the uppercase one, so a proxy exported in
# only one case silently applies to only some of the tools -- which looks like
# "the installer works up to the Python step".
for _pv in http_proxy https_proxy no_proxy; do
    # tr, not ${_pv^^}: uppercasing expansions needs bash 4 and macOS ships 3.2.
    _pv_upper="$(printf '%s' "$_pv" | tr 'a-z' 'A-Z')"
    eval "_pv_lower_val=\${$_pv:-}"
    eval "_pv_upper_val=\${$_pv_upper:-}"
    if [ -n "$_pv_lower_val" ] && [ -z "$_pv_upper_val" ]; then
        export "$_pv_upper=$_pv_lower_val"
    elif [ -n "$_pv_upper_val" ] && [ -z "$_pv_lower_val" ]; then
        export "$_pv=$_pv_upper_val"
    fi
done
unset _pv _pv_upper _pv_lower_val _pv_upper_val
if [ -n "${HTTPS_PROXY:-}" ]; then
    # ##*@ strips any credentials: HTTPS_PROXY frequently carries
    # user:password@host, and echoing it would put a secret in the terminal
    # scrollback and in any CI log that captures this run.
    echo "Using proxy: ${HTTPS_PROXY##*@}"
fi

# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------
REPO_URL="https://github.com/palindrome-rl/AGENT8088.git"
REPO_BRANCH="${AGENT8088_BRANCH:-main}"
AGENT8088_HOME="${AGENT8088_HOME:-$HOME/.agent8088}"
INSTALL_DIR="$AGENT8088_HOME/agent8088"
PYTHON_VERSION="3.11"
PYTHON_FALLBACK_VERSIONS=("3.12" "3.10")
NODE_VERSION="22.11.0"

# Options
SKIP_SETUP=false
BRANCH="$REPO_BRANCH"
IS_INTERACTIVE=true
FRESH_INSTALL=false
CONFIG_CREATED=false
INITIAL_SETUP_RAN=false
# Readiness flags set by the new stages so verify_install can report actual state.
GATEWAY_EXTRAS_INSTALLED=false
SEARCH_EXTRAS_INSTALLED=false
CHROMIUM_INSTALLED=false
NODE_INSTALLED=false
WHATSAPP_BRIDGE_READY=false
SANDBOX_INSTALLED=false

# Detect non-interactive mode (curl | bash). When stdin is not a terminal,
# read -p fails with EOF, causing set -e to abort.
if [ -t 0 ]; then
    IS_INTERACTIVE=true
else
    IS_INTERACTIVE=false
fi

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --skip-setup) SKIP_SETUP=true; shift ;;
        --branch)     BRANCH="$2"; shift 2 ;;
        -h|--help)
            echo "Agent8088 Installer"
            echo ""
            echo "Usage: curl -fsSL <url> | bash -s -- [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --skip-setup   Skip interactive setup wizard"
            echo "  --branch NAME  Git branch to install (default: $REPO_BRANCH)"
            echo "  -h, --help      Show this help"
            exit 0
            ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

# ----------------------------------------------------------------------------
# Helper functions
# ----------------------------------------------------------------------------
log_info()    { echo -e "\033[0;36m→\033[0m $1"; }
log_success() { echo -e "\033[0;32m✓\033[0m $1"; }
log_warn()    { echo -e "\033[0;33m⚠\033[0m $1"; }
log_error()   { echo -e "\033[0;31m✗\033[0m $1"; }

# ----------------------------------------------------------------------------
# Timeouts for the network stages
# ----------------------------------------------------------------------------
# Every optional stage already tolerated a *failure* (`|| true` + log_warn), but
# nothing protected any stage from a *hang*. A stalled `ollama pull`, an npm
# registry that accepts the connection and then goes quiet, a wedged Ollama
# daemon, or a package download that dribbles bytes forever left the installer
# waiting indefinitely with no way out but Ctrl-C.
#
# Limits are deliberately moderate rather than maximally generous: an optional
# stage degrades to a "run this to fix it later" message, so the cost of cutting
# a slow-but-working download short is one rerun, while the cost of waiting too
# long is an installer that looks frozen. Sized so a ~4 Mbps link finishes
# comfortably.
#
# Scale them all for a slow connection:
#   curl -fsSL <url> | AGENT8088_TIMEOUT_SCALE=3 bash
TIMEOUT_SCALE="${AGENT8088_TIMEOUT_SCALE:-1}"
case "$TIMEOUT_SCALE" in
    ''|*[!0-9]*) TIMEOUT_SCALE=1 ;;
esac
[ "$TIMEOUT_SCALE" -lt 1 ] && TIMEOUT_SCALE=1

T_OLLAMA_CHECK=$((15  * TIMEOUT_SCALE))   # nothing, local - instant unless the daemon is wedged
T_OLLAMA_PULL=$((600  * TIMEOUT_SCALE))   # 274 MB embedding model
T_NPM=$((300          * TIMEOUT_SCALE))   # 142 small packages, mostly round-trips
T_CHROMIUM=$((600     * TIMEOUT_SCALE))   # ~150 MB browser download
T_NODE_DL=$((180      * TIMEOUT_SCALE))   # ~30 MB tarball
T_PIP=$((300          * TIMEOUT_SCALE))   # gateway extras: tens of MB of wheels
T_GIT=$((600          * TIMEOUT_SCALE))   # shallow clone: small, but a stalled fetch hangs
# The core editable install is the stage that actually hangs: it pulls
# playwright's and ddgs's native wheels plus mcp and Pillow. Not optional, so a
# premature cut fails the install outright -- but it is still the largest
# download set here, so it gets the same 10m ceiling as Chromium rather than a
# looser one. AGENT8088_TIMEOUT_SCALE raises it for a genuinely slow link.
T_CORE_INSTALL=$((600 * TIMEOUT_SCALE))
T_VENV=$((300         * TIMEOUT_SCALE))   # uv may download a CPython build
T_UV_BOOT=$((300      * TIMEOUT_SCALE))   # uv self-installer

# Does this `timeout` understand -k (kill-after)? GNU coreutils >= 7 and busybox
# >= 1.30 do; older busybox treats -k as the command name and would run the
# wrong thing entirely. Probed once, because getting it wrong is silent.
_TIMEOUT_HAS_K=""
_timeout_supports_k() {
    if [ -z "$_TIMEOUT_HAS_K" ]; then
        if timeout -k 1 1 true >/dev/null 2>&1; then _TIMEOUT_HAS_K=yes
        else _TIMEOUT_HAS_K=no
        fi
    fi
    [ "$_TIMEOUT_HAS_K" = yes ]
}

# Run a command under a wall-clock limit. macOS ships no `timeout` (it is GNU
# coreutils), so prefer timeout/gtimeout where they exist and fall back to a
# background watchdog that escalates TERM -> KILL.
#
# -k 10 matters: plain SIGTERM loses to a child that traps or ignores it (npm's
# node wrapper does), so the child survives its own timeout and the installer
# hangs anyway -- the "the timeout does not work" symptom.
#
# Returns 124 on timeout, matching GNU timeout, so callers can tell a hang from
# an ordinary failure. A SIGKILLed child surfaces as 137 and a SIGTERMed one as
# 143, so both are normalized to 124 -- warn_stage only recognizes 124 as a hang.
# Must not let `wait` trip `set -e`.
run_with_timeout() {
    local _secs="$1"; shift
    local _rc=0

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

    "$@" &
    local _pid=$!
    (
        _waited=0
        while [ "$_waited" -lt "$_secs" ]; do
            kill -0 "$_pid" 2>/dev/null || exit 0
            sleep 1
            _waited=$((_waited + 1))
        done
        kill -TERM "$_pid" 2>/dev/null || true
        sleep 10
        kill -KILL "$_pid" 2>/dev/null || true
    ) >/dev/null 2>&1 &
    local _watchdog=$!

    wait "$_pid" 2>/dev/null || _rc=$?
    kill -KILL "$_watchdog" 2>/dev/null || true
    wait "$_watchdog" 2>/dev/null || true

    case "$_rc" in 137|143) _rc=124 ;; esac
    return $_rc
}

# Skipped-stage ledger, printed as one block at the end of the run.
#
# Warnings are emitted as each stage runs, which on a multi-minute install means
# they have scrolled well out of view by the time it finishes - the WhatsApp
# bridge failing was reported and still went unnoticed. Recording them lets the
# final summary state plainly what did not install and how to fix each one.
# Fields are tab-separated: label, reason, fix command.
SKIPPED_STAGES=()

# NOTE for future edits: this appends to a shell array, so it must be called at
# statement level. A call inside a pipeline or `( ... )` runs in a subshell, the
# append is discarded when that subshell exits, and the stage then vanishes from
# the final summary while still printing its warning -- a silent half-failure.
record_skip() {
    SKIPPED_STAGES+=("$1"$'\t'"$2"$'\t'"${3:-}")
}

# Warn about a stage that did not complete, naming a hang as a hang. "timed out
# after 10m" and "failed" point at different fixes. The optional 5th argument is
# the command that repairs it, surfaced again in the final summary.
warn_stage() {
    local _rc="$1" _secs="$2" _what="$3" _consequence="$4" _fix="${5:-}"
    local _reason
    if [ "$_rc" -eq 124 ]; then
        _reason="timed out after $((_secs / 60))m"
        log_warn "$_what timed out after $((_secs / 60))m - $_consequence"
        log_warn "On a slow connection, rerun the installer with AGENT8088_TIMEOUT_SCALE=3"
    else
        _reason="failed (exit $_rc)"
        log_warn "$_what failed - $_consequence"
    fi
    record_skip "$_what" "$_reason" "$_fix"
}

# Final block: what did not install, why, and the command that fixes it. Silent
# when everything succeeded.
print_skipped_summary() {
    [ "${#SKIPPED_STAGES[@]}" -eq 0 ] && return 0
    local _entry _label _reason _fix
    echo ""
    echo -e "\033[0;33m${#SKIPPED_STAGES[@]} optional component(s) did not install:\033[0m"
    for _entry in "${SKIPPED_STAGES[@]}"; do
        IFS=$'\t' read -r _label _reason _fix <<< "$_entry"
        echo -e "  \033[0;33m•\033[0m $_label — $_reason"
        [ -n "$_fix" ] && echo "      fix: $_fix"
    done
    echo ""
    echo "  The core agent is installed and works without these."
}

is_termux() {
    [ -n "${TERMUX_VERSION:-}" ] || [[ "${PREFIX:-}" == *"com.termux/files/usr"* ]]
}

prompt_yes_no() {
    local question="$1"
    local default="${2:-yes}"
    local prompt_suffix answer=""
    case "$default" in
        y|Y|yes|YES|true|1) prompt_suffix="[Y/n]" ;;
        *) prompt_suffix="[y/N]" ;;
    esac
    if [ "$IS_INTERACTIVE" = true ]; then
        read -r -p "$question $prompt_suffix " answer || answer=""
    elif [ -r /dev/tty ] && [ -w /dev/tty ]; then
        printf "%s %s " "$question" "$prompt_suffix" > /dev/tty
        IFS= read -r answer < /dev/tty || answer=""
    else
        answer=""
    fi
    if [ -z "$answer" ]; then
        case "$default" in y|Y|yes|YES|true|1) return 0 ;; *) return 1 ;; esac
    fi
    case "$answer" in y|Y|yes|YES) return 0 ;; *) return 1 ;; esac
}

print_banner() {
    echo ""
    echo -e "\033[0;35m\033[1m"
    echo "┌─────────────────────────────────────────────────────────┐"
    echo "│             ⚡ Agent8088 Installer                       │"
    echo "├─────────────────────────────────────────────────────────┤"
    echo "│  A local AI agent by Palindrome Research Labs.          │"
    echo "└─────────────────────────────────────────────────────────┘"
    echo -e "\033[0m"
}

# ----------------------------------------------------------------------------
# OS detection
# ----------------------------------------------------------------------------
detect_os() {
    case "$(uname -s)" in
        Linux*)
            if is_termux; then
                OS="android"; DISTRO="termux"
            else
                OS="linux"
                if [ -f /etc/os-release ]; then
                    # Read in a subshell. Sourcing it directly defines NAME,
                    # VERSION, ID, PRETTY_NAME, HOME_URL and more in the
                    # installer's own shell, where they can collide with names
                    # used later -- a distro file is not ours to trust with our
                    # namespace.
                    DISTRO="$(. /etc/os-release 2>/dev/null && printf '%s' "${ID:-unknown}")"
                    DISTRO_VERSION="$(. /etc/os-release 2>/dev/null && printf '%s' "${VERSION_ID:-}")"
                else
                    DISTRO="unknown"; DISTRO_VERSION=""
                fi
            fi
            ;;
        Darwin*)
            OS="macos"; DISTRO="macos"
            ;;
        CYGWIN*|MINGW*|MSYS*)
            OS="windows"; DISTRO="windows"
            log_error "Windows detected. Please use the PowerShell installer:"
            log_info "  iex (irm https://<YOUR-URL>/install.ps1)"
            exit 1
            ;;
        *)
            # Previously this warned and carried on, so a BSD reached the uv/venv
            # stages and failed there with something that named neither the OS nor
            # the real problem. Refuse here, where the message can be useful.
            log_error "Unsupported operating system: $(uname -s)"
            log_info "Supported: Linux, macOS, WSL2. Windows uses install.ps1."
            log_info "On another Unix, install manually:  pip install agent8088"
            exit 1
            ;;
    esac
    log_success "Detected: $OS ($DISTRO)"
}

# ----------------------------------------------------------------------------
# Stage 1: Install uv (managed, into ~/.agent8088/bin)
# ----------------------------------------------------------------------------
install_uv() {
    if [ "$DISTRO" = "termux" ]; then
        log_info "Termux detected — using Python's stdlib venv + pip instead of uv"
        UV_CMD=""
        return 0
    fi

    local _managed_uv="$AGENT8088_HOME/bin/uv"
    if [ -x "$_managed_uv" ]; then
        UV_CMD="$_managed_uv"
        log_success "Managed uv found ($($UV_CMD --version 2>/dev/null))"
        return 0
    fi

    log_info "Installing managed uv into $AGENT8088_HOME/bin ..."
    mkdir -p "$AGENT8088_HOME/bin"

    # Download to temp file first — `curl | sh` masks curl failures (sh exits 0
    # on empty stdin).
    local _uv_installer
    _uv_installer="$(mktemp 2>/dev/null || echo "/tmp/agent8088-uv.$$.sh")"
    # Routed through _download_file (curl-or-wget, same TLS/proto pin as the
    # Node tarball download below) rather than bare curl: a curl-less-but-
    # wget-having host would otherwise die right here, on this mandatory
    # bootstrap step, before ever reaching the fallback Part B protects.
    if ! _download_file "https://astral.sh/uv/install.sh" "$_uv_installer" 120; then
        log_error "Failed to download uv installer from https://astral.sh/uv/install.sh"
        log_info "Install manually: https://docs.astral.sh/uv/getting-started/installation/"
        rm -f "$_uv_installer"
        exit 1
    fi
    _uv_boot_rc=0
    run_with_timeout "$T_UV_BOOT" \
        env UV_UNMANAGED_INSTALL="$AGENT8088_HOME/bin" sh "$_uv_installer" >/dev/null 2>&1 \
        || _uv_boot_rc=$?
    if [ "$_uv_boot_rc" -eq 124 ]; then
        log_error "The uv installer timed out after $((T_UV_BOOT / 60))m"
        log_info "Install manually: https://docs.astral.sh/uv/getting-started/installation/"
        rm -f "$_uv_installer"
        exit 1
    fi
    if [ "$_uv_boot_rc" -eq 0 ]; then
        rm -f "$_uv_installer"
        if [ -x "$_managed_uv" ]; then
            UV_CMD="$_managed_uv"
            log_success "Managed uv installed ($($UV_CMD --version 2>/dev/null))"
        else
            log_error "uv installer reported success but binary not found at $_managed_uv"
            exit 1
        fi
    else
        log_error "Failed to install uv"
        log_info "Install manually: https://docs.astral.sh/uv/getting-started/installation/"
        rm -f "$_uv_installer"
        exit 1
    fi
}

# ----------------------------------------------------------------------------
# Stage 2: Find or install Python
# ----------------------------------------------------------------------------
check_python() {
    if [ "$DISTRO" = "termux" ]; then
        log_info "Checking Termux Python..."
        if command -v python >/dev/null 2>&1; then
            PYTHON_PATH="$(command -v python)"
            if "$PYTHON_PATH" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
                log_success "Python found: $("$PYTHON_PATH" --version 2>/dev/null)"
                return 0
            fi
        fi
        log_info "Installing Python via pkg..."
        pkg install -y python >/dev/null
        PYTHON_PATH="$(command -v python)"
        log_success "Python installed: $("$PYTHON_PATH" --version 2>/dev/null)"
        return 0
    fi

    log_info "Checking Python $PYTHON_VERSION..."
    if PYTHON_PATH="$("$UV_CMD" python find "$PYTHON_VERSION" 2>/dev/null)"; then
        log_success "Python found: $("$PYTHON_PATH" --version 2>/dev/null)"
        return 0
    fi

    log_info "Python $PYTHON_VERSION not found, installing via uv..."
    if "$UV_CMD" python install "$PYTHON_VERSION" >/dev/null 2>&1; then
        PYTHON_PATH="$("$UV_CMD" python find "$PYTHON_VERSION")"
        log_success "Python installed: $("$PYTHON_PATH" --version 2>/dev/null)"
        return 0
    fi

    # Fallback: try fallback versions, then any system Python 3.10+
    log_info "Trying fallback Python versions..."
    for fallback_ver in "${PYTHON_FALLBACK_VERSIONS[@]}"; do
        if PYTHON_PATH="$("$UV_CMD" python find "$fallback_ver" 2>/dev/null)"; then
            log_success "Found fallback: $("$PYTHON_PATH" --version 2>/dev/null)"
            return 0
        fi
    done

    log_error "Failed to find or install Python $PYTHON_VERSION"
    log_info "Install Python 3.11 manually, then re-run this script"
    exit 1
}

# ----------------------------------------------------------------------------
# Stage 3: Install git
# ----------------------------------------------------------------------------
check_git() {
    log_info "Checking Git..."
    if command -v git >/dev/null 2>&1 && git --version >/dev/null 2>&1; then
        log_success "Git $(git --version | awk '{print $3}') found"
        return 0
    fi

    log_warn "Git not found"
    if [ "$DISTRO" = "termux" ]; then
        log_info "Installing Git via pkg..."
        pkg install -y git >/dev/null 2>&1 || true
        if command -v git >/dev/null 2>&1; then
            log_success "Git installed"
            return 0
        fi
    fi

    # Try automatic install
    log_info "Attempting to install Git automatically..."
    case "$OS" in
        macos)
            if command -v brew >/dev/null 2>&1; then
                log_info "Installing Git via Homebrew..."
                brew install git >/dev/null 2>&1 || true
            fi
            if command -v git >/dev/null 2>&1; then
                log_success "Git installed via Homebrew"
                return 0
            fi
            if command -v xcode-select >/dev/null 2>&1; then
                log_info "Requesting Apple Command Line Tools..."
                log_info "If a macOS dialog appears, click \"Install\"."
                xcode-select --install >/dev/null 2>&1 || true
                local waited=0
                while [ "$waited" -lt 300 ]; do
                    if command -v git >/dev/null 2>&1 && git --version >/dev/null 2>&1; then
                        log_success "Git installed via Command Line Tools"
                        return 0
                    fi
                    sleep 5; waited=$((waited + 5))
                done
            fi
            ;;
        linux)
            local sudo_cmd=""
            [ "$(id -u 2>/dev/null || echo 1000)" -ne 0 ] && command -v sudo >/dev/null 2>&1 && sudo_cmd="sudo"
            case "$DISTRO" in
                ubuntu|debian)
                    log_info "Installing Git via apt..."
                    $sudo_cmd env DEBIAN_FRONTEND=noninteractive apt-get update -qq >/dev/null 2>&1 || true
                    $sudo_cmd env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git >/dev/null 2>&1 || true
                    ;;
                fedora)
                    log_info "Installing Git via dnf..."
                    $sudo_cmd dnf install -y git >/dev/null 2>&1 || true
                    ;;
                arch)
                    log_info "Installing Git via pacman..."
                    $sudo_cmd pacman -S --noconfirm git >/dev/null 2>&1 || true
                    ;;
            esac
            if command -v git >/dev/null 2>&1; then
                log_success "Git installed"
                return 0
            fi
            ;;
    esac

    log_error "Could not install Git automatically. Please install it manually:"
    case "$OS" in
        linux)
            case "$DISTRO" in
                ubuntu|debian) log_info "  sudo apt install git" ;;
                fedora)       log_info "  sudo dnf install git" ;;
                arch)         log_info "  sudo pacman -S git" ;;
                *)            log_info "  Use your package manager to install git" ;;
            esac
            ;;
        android) log_info "  pkg install git" ;;
        macos)   log_info "  xcode-select --install  (or: brew install git)" ;;
    esac
    exit 1
}

# ----------------------------------------------------------------------------
# Stage 4: Clone repo
# ----------------------------------------------------------------------------
clone_repo() {
    log_info "Installing to $INSTALL_DIR..."

    # Suppress git credential prompts - the repo is public, anonymous clone
    # works. Without this, git may prompt for username/password on HTTPS.
    export GIT_TERMINAL_PROMPT=0

    # An interrupted previous clone leaves .git with no initial commit.
    if [ -d "$INSTALL_DIR/.git" ] && ! git -C "$INSTALL_DIR" rev-parse --verify HEAD >/dev/null 2>&1; then
        local backup_dir="${INSTALL_DIR}.broken-$(date -u +%Y%m%d-%H%M%S)"
        log_warn "Existing checkout at $INSTALL_DIR has no commits (interrupted clone)."
        log_warn "Moving it aside to $backup_dir before re-cloning."
        mv "$INSTALL_DIR" "$backup_dir"
    fi

    if [ -d "$INSTALL_DIR/.git" ]; then
        log_info "Existing installation found, updating..."
        cd "$INSTALL_DIR"
        git config core.autocrlf false
        if [ -n "$(git status --porcelain)" ]; then
            # Clear unmerged index entries from a previous conflict
            if [ -n "$(git ls-files --unmerged)" ]; then
                log_info "Clearing unmerged index entries..."
                git reset -q
            fi
            log_info "Local changes detected, stashing before update..."
            git stash push --include-untracked -m "agent8088-install-autostash-$(date -u +%Y%m%d-%H%M%S)" >/dev/null 2>&1 || true
        fi
        git remote set-url origin "$REPO_URL" 2>/dev/null || true
        # GIT_HTTP_LOW_SPEED_* bounds the byte-moving part; this bounds the rest
        # (ref negotiation, local object write), which those variables do not cover.
        run_with_timeout "$T_GIT" git fetch --depth 1 origin "$BRANCH" >/dev/null 2>&1 || {
            log_error "git fetch timed out or failed after $((T_GIT / 60))m"
            log_error "Check your connection, then rerun. On a slow link: AGENT8088_TIMEOUT_SCALE=3"
            exit 1
        }
        git checkout -B "$BRANCH" FETCH_HEAD >/dev/null 2>&1
        git reset --hard FETCH_HEAD >/dev/null 2>&1
    else
        log_info "Cloning Agent8088 repository..."
        rm -rf "$INSTALL_DIR"
        mkdir -p "$AGENT8088_HOME"
        run_with_timeout "$T_GIT" git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR" || {
            log_error "git clone timed out or failed after $((T_GIT / 60))m"
            log_error "Check your connection, then rerun. On a slow link: AGENT8088_TIMEOUT_SCALE=3"
            exit 1
        }
        cd "$INSTALL_DIR"
        git config core.autocrlf false
        FRESH_INSTALL=true
    fi
    local installed_commit
    installed_commit="$(git -C "$INSTALL_DIR" rev-parse --short HEAD 2>/dev/null || echo unknown)"
    log_success "Repository ready at $INSTALL_DIR ($BRANCH@$installed_commit)"
}

# Classifies how a privileged command (currently just `playwright
# install-deps`) may run, before anything privileged is actually attempted:
#   direct  - already root, no sudo needed
#   sudo    - a NOPASSWD rule or an already-cached sudo timestamp; -n works
#   prompt  - sudo is present and would need a real password, but a real
#             terminal is attached to type it into (either this script's own
#             stdin, or /dev/tty when stdin is a `curl | bash` pipe)
#   skip    - no sudo binary, or no terminal anywhere to prompt on
#
# Kept separate from running anything so the caller can tell "safe
# non-interactively" apart from "would need to prompt" and pick a strategy
# BEFORE risking a privileged command that might block on input nobody can see.
_privileged_run_mode() {
    if [ "$(id -u 2>/dev/null || echo 1000)" -eq 0 ]; then
        echo "direct"
        return
    fi
    if ! command -v sudo >/dev/null 2>&1; then
        echo "skip"
        return
    fi
    if sudo -n true >/dev/null 2>&1; then
        echo "sudo"
        return
    fi
    if [ -t 0 ] || { [ -r /dev/tty ] && [ -w /dev/tty ]; }; then
        echo "prompt"
    else
        echo "skip"
    fi
}

# ----------------------------------------------------------------------------
# Stage 5: Create venv + install the package
# ----------------------------------------------------------------------------
install_deps() {
    local _py="$INSTALL_DIR/venv/bin/python"
    if [ "$DISTRO" = "termux" ]; then
        log_info "Creating venv (stdlib) and installing via pip..."
        run_with_timeout "$T_VENV" python -m venv "$INSTALL_DIR/venv" || {
            log_error "venv creation timed out or failed"; exit 1; }
        # shellcheck disable=SC1091
        . "$INSTALL_DIR/venv/bin/activate"
        # Cosmetic, and it failing must not abort an otherwise-fine install.
        run_with_timeout "$T_CORE_INSTALL" pip install --upgrade pip >/dev/null 2>&1 || true
        _core_rc=0
        run_with_timeout "$T_CORE_INSTALL" pip install --upgrade --force-reinstall -e . \
            >/dev/null 2>&1 || _core_rc=$?
        if [ "$_core_rc" -eq 124 ]; then
            log_error "pip install timed out after $((T_CORE_INSTALL / 60))m - a package download stalled."
            log_error "Retry on a slower link with: AGENT8088_TIMEOUT_SCALE=3"
            exit 1
        elif [ "$_core_rc" -ne 0 ]; then
            log_error "pip install failed (exit $_core_rc)"
            exit 1
        fi
    else
        log_info "Creating venv and installing via uv..."
        # Re-running the installer over an existing install is a supported path —
        # the caller was told "Existing installation found, updating..." further
        # up. Plain `uv venv` contradicts that: it exits 2 with "A virtual
        # environment already exists ... Use --clear to replace it". With set -e
        # and output on /dev/null, that ended the installer here with no error
        # printed, no venv touched, and nothing to go on.
        #
        # --allow-existing reuses the venv, so an update keeps the packages it
        # already has instead of re-downloading them.
        if ! run_with_timeout "$T_VENV" "$UV_CMD" venv --python "$PYTHON_PATH" \
                --allow-existing "$INSTALL_DIR/venv" >/dev/null 2>&1 \
                || [ ! -x "$_py" ]; then
            # Reuse can legitimately fail: a venv built by a Python that has
            # since been removed or upgraded, or one left half-written by an
            # interrupted run. That is not something to hand back to the user as
            # a decision — rebuild it.
            log_warn "Existing virtualenv is not usable — rebuilding it"
            run_with_timeout "$T_VENV" "$UV_CMD" venv --python "$PYTHON_PATH" \
                    --clear "$INSTALL_DIR/venv" >/dev/null 2>&1 || {
                log_error "Could not create the virtualenv at $INSTALL_DIR/venv"
                log_error "Run this to see the underlying error:"
                log_error "  $UV_CMD venv --python $PYTHON_PATH --clear $INSTALL_DIR/venv"
                log_error "If it keeps failing, remove the install and start clean:"
                log_error "  agent8088 --uninstall"
                exit 1
            }
        fi
        # This is the stage that actually hangs: playwright's and ddgs's native
        # wheels plus mcp and Pillow. Mandatory, so a timeout is a hard failure
        # with a specific message rather than a skip.
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
            log_error "uv pip install failed (exit $_core_rc); retrying with --reinstall"
            _core_rc=0
            run_with_timeout "$T_CORE_INSTALL" "$UV_CMD" pip install --python "$_py" \
                --reinstall -e "$INSTALL_DIR" >/dev/null 2>&1 || _core_rc=$?
            if [ "$_core_rc" -ne 0 ]; then
                if [ "$_core_rc" -eq 124 ]; then
                    log_error "Retry also timed out after $((T_CORE_INSTALL / 60))m"
                    log_error "Retry on a slower link with: AGENT8088_TIMEOUT_SCALE=3"
                else
                    log_error "Failed to install agent8088 (exit $_core_rc)"
                fi
                exit 1
            fi
        fi
    fi
    log_success "agent8088 installed (editable)"

    # --- Gateway adapter Python extras (Slack, Discord, WhatsApp, Telegram) ---
    # The [gateway] extra from pyproject.toml: slack-bolt, slack-sdk, httpx,
    # discord.py, python-telegram-bot. Without these, runner.py:463-497 guards
    # each adapter with try/except ImportError and silently no-ops.
    log_info "Installing gateway adapter dependencies (Slack, Discord, WhatsApp, Telegram)..."
    _gw_rc=0
    if [ "$DISTRO" = "termux" ]; then
        run_with_timeout "$T_PIP" pip install -e ".[gateway]" >/dev/null 2>&1 || _gw_rc=$?
    else
        run_with_timeout "$T_PIP" "$UV_CMD" pip install --python "$_py" \
            -e "$INSTALL_DIR[gateway]" >/dev/null 2>&1 || _gw_rc=$?
    fi
    if [ "$_gw_rc" -eq 0 ]; then
        GATEWAY_EXTRAS_INSTALLED=true
    else
        warn_stage "$_gw_rc" "$T_PIP" "Gateway adapter extras" \
            "Slack/Discord/Telegram adapters unavailable" \
            "$UV_CMD pip install --python $_py -e \"$INSTALL_DIR[gateway]\""
    fi
    [ "$GATEWAY_EXTRAS_INSTALLED" = true ] && log_success "Gateway adapters installed"

    # --- Keyless web search backend (optional [search] extra) ---
    # Installed everywhere so web_search keeps its no-key fallback; non-fatal
    # because ddgs->primp has no Android wheel and cannot build under Termux.
    log_info "Installing keyless web search backend (ddgs)..."
    _search_rc=0
    if [ "$DISTRO" = "termux" ]; then
        run_with_timeout "$T_PIP" pip install -e ".[search]" >/dev/null 2>&1 || _search_rc=$?
    else
        run_with_timeout "$T_PIP" "$UV_CMD" pip install --python "$_py" \
            -e "$INSTALL_DIR[search]" >/dev/null 2>&1 || _search_rc=$?
    fi
    if [ "$_search_rc" -eq 0 ]; then
        SEARCH_EXTRAS_INSTALLED=true
    else
        warn_stage "$_search_rc" "$T_PIP" "Keyless web search backend (ddgs)" \
            "configure SearXNG or an API-key backend for web_search" \
            "$UV_CMD pip install --python $_py -e \"$INSTALL_DIR[search]\""
    fi
    [ "$SEARCH_EXTRAS_INSTALLED" = true ] && log_success "Keyless web search backend installed"

    # --- Playwright (optional [browser] extra) + Chromium binary ---
    # playwright degrades gracefully at runtime (engine.py: _playwright_available()),
    # so it's an optional extra, not a core dep - a platform/Python combo without
    # wheels for it (e.g. Termux landing on a brand-new Python minor) shouldn't
    # fail the whole install.
    log_info "Installing Playwright (optional, for browse_page)..."
    local _playwright_installed=false
    _pw_rc=0
    if [ "$DISTRO" = "termux" ]; then
        run_with_timeout "$T_PIP" pip install -e ".[browser]" >/dev/null 2>&1 || _pw_rc=$?
    else
        run_with_timeout "$T_PIP" "$UV_CMD" pip install --python "$_py" \
            -e "$INSTALL_DIR[browser]" >/dev/null 2>&1 || _pw_rc=$?
    fi
    if [ "$_pw_rc" -eq 0 ]; then
        _playwright_installed=true
    else
        warn_stage "$_pw_rc" "$T_PIP" "Playwright" \
            "browse_page will show install instructions" \
            "$UV_CMD pip install --python $_py -e \"$INSTALL_DIR[browser]\""
    fi
    if [ "$_playwright_installed" = true ]; then
        log_info "Installing Playwright Chromium browser (~280 MB)..."
        _chromium_rc=0
        run_with_timeout "$T_CHROMIUM" "$_py" -m playwright install chromium \
            >/dev/null 2>&1 || _chromium_rc=$?
        if [ "$_chromium_rc" -eq 0 ]; then
            CHROMIUM_INSTALLED=true
        else
            warn_stage "$_chromium_rc" "$T_CHROMIUM" "Chromium browser" \
                "browse_page will show install instructions" \
                "$_py -m playwright install chromium"
        fi
        if [ "$CHROMIUM_INSTALLED" = true ] && [ "$OS" = "linux" ]; then
            log_info "Installing Playwright Chromium system dependencies..."
            local _priv_mode
            _priv_mode="$(_privileged_run_mode)"
            case "$_priv_mode" in
                direct)
                    run_with_timeout "$T_PIP" "$_py" -m playwright install-deps chromium \
                        >/dev/null 2>&1 || \
                        log_warn "Playwright system dependencies were not installed - run: playwright install-deps chromium"
                    ;;
                sudo)
                    # Passwordless: either a NOPASSWD sudoers rule, or a sudo
                    # timestamp already cached earlier in this same session. `-n`
                    # on the real command too, so a timestamp that expires
                    # between this check and the call fails closed instead of
                    # prompting.
                    run_with_timeout "$T_PIP" sudo -n "$_py" -m playwright install-deps chromium \
                        >/dev/null 2>&1 || \
                        log_warn "Playwright system dependencies were not installed - run: sudo playwright install-deps chromium"
                    ;;
                prompt)
                    # A real password is needed, and a real terminal is
                    # attached to type it into (either stdin, or /dev/tty when
                    # stdin is a `curl | bash` pipe). `sudo -v` only
                    # authenticates; it has no noisy output of its own to
                    # hide, so unlike every other stage here it must NOT be
                    # silenced with >/dev/null 2>&1 -- that would hide the
                    # "[sudo] password for" prompt behind what looks like a
                    # stalled install. Once the credential is cached, the
                    # actual install-deps run reuses the quiet `sudo -n` call
                    # from the passwordless case above.
                    log_info "Playwright's system dependencies need sudo - you may be prompted for your password."
                    local _sudo_authed=0
                    if [ -t 0 ]; then
                        run_with_timeout "$T_PIP" sudo -v && _sudo_authed=1
                    else
                        run_with_timeout "$T_PIP" sudo -v </dev/tty && _sudo_authed=1
                    fi
                    if [ "$_sudo_authed" -eq 1 ]; then
                        run_with_timeout "$T_PIP" sudo -n "$_py" -m playwright install-deps chromium \
                            >/dev/null 2>&1 || \
                            log_warn "Playwright system dependencies were not installed - run: sudo $_py -m playwright install-deps chromium"
                    else
                        log_warn "Playwright system dependencies were not installed (sudo authentication failed or timed out)"
                        log_info "  Run manually if browse_page needs it: sudo $_py -m playwright install-deps chromium"
                    fi
                    ;;
                *)
                    # No sudo at all, or no terminal to prompt on (fully
                    # non-interactive, e.g. CI) -- nothing to prompt into.
                    log_warn "Playwright system dependencies need a sudo password - skipping (no terminal to prompt on)"
                    log_info "  Run manually if browse_page needs it: sudo $_py -m playwright install-deps chromium"
                    ;;
            esac
        fi
        [ "$CHROMIUM_INSTALLED" = true ] && log_success "Chromium installed for browse_page"
    fi
}

# ----------------------------------------------------------------------------
# Stage 5b: Node.js (for WhatsApp bridge) + npm install
# ----------------------------------------------------------------------------
# WhatsApp's bridge is a Node.js process (Baileys). Without Node on PATH the
# adapter errors at connect() time. We ensure Node >= 20.11 is available
# (brew/package manager/download), then npm install in the bridge dir so
# node_modules is materialized for the bridge to require().
install_node_bridge() {
    # --- 1. Ensure Node >= 20.11 is available ------------------------------
    local _node_ok=false
    if command -v node >/dev/null 2>&1; then
        local _ver
        _ver="$(node --version 2>/dev/null | sed 's/^v//')"
        local _major="${_ver%%.*}"
        if [ -n "$_major" ] && [ "$_major" -ge 20 ]; then
            log_success "Node $_ver found on PATH"
            _node_ok=true
        else
            log_warn "Node $_ver found but < 20 - will install newer Node"
        fi
    fi

    # Check managed Node in $AGENT8088_HOME/node
    if [ "$_node_ok" = false ] && [ -x "$AGENT8088_HOME/node/bin/node" ]; then
        local _ver
        _ver="$("$AGENT8088_HOME/node/bin/node" --version 2>/dev/null | sed 's/^v//')"
        local _major="${_ver%%.*}"
        if [ -n "$_major" ] && [ "$_major" -ge 20 ]; then
            export PATH="$AGENT8088_HOME/node/bin:$PATH"
            log_success "Managed Node $_ver found"
            _node_ok=true
        fi
    fi

    # Install Node if still needed
    if [ "$_node_ok" = false ]; then
        local _did_install=false
        case "$OS" in
            macos)
                if command -v brew >/dev/null 2>&1; then
                    log_info "Installing Node via Homebrew..."
                    brew install node >/dev/null 2>&1 && _did_install=true || true
                fi
                ;;
            linux)
                local sudo_cmd=""
                [ "$(id -u 2>/dev/null || echo 1000)" -ne 0 ] && command -v sudo >/dev/null 2>&1 && sudo_cmd="sudo"
                case "$DISTRO" in
                    ubuntu|debian)
                        # Ubuntu/Debian ship ancient Node (12.x) in apt - too old
                        # for sandbox-runtime (needs 20.11+). Skip apt and use the
                        # portable tarball fallback below instead of wasting time
                        # on a package that will fail the version check.
                        log_info "Skipping apt nodejs (too old on $DISTRO) - using portable download"
                        ;;
                    fedora)
                        log_info "Installing Node via dnf..."
                        $sudo_cmd dnf install -y nodejs npm >/dev/null 2>&1 && _did_install=true || true
                        ;;
                    arch)
                        log_info "Installing Node via pacman..."
                        $sudo_cmd pacman -S --noconfirm nodejs npm >/dev/null 2>&1 && _did_install=true || true
                        ;;
                esac
                ;;
            android)
                if is_termux; then
                    log_info "Installing Node via pkg..."
                    pkg install -y nodejs >/dev/null 2>&1 && _did_install=true || true
                fi
                ;;
        esac

        # Verify package-manager install worked
        if [ "$_did_install" = true ] && command -v node >/dev/null 2>&1; then
            local _ver
            _ver="$(node --version 2>/dev/null | sed 's/^v//')"
            local _major="${_ver%%.*}"
            if [ -n "$_major" ] && [ "$_major" -ge 20 ]; then
                log_success "Node $_ver installed via package manager"
                _node_ok=true
            else
                log_warn "Installed Node $_ver is still < 20"
                _did_install=false
            fi
        fi

        # Fallback: download a portable Node tarball (no admin needed)
        if [ "$_node_ok" = false ] && [ "$OS" != "android" ]; then
            log_info "Downloading portable Node $NODE_VERSION..."
            local _arch
            case "$(uname -m)" in
                x86_64|amd64) _arch="x64" ;;
                aarch64|arm64) _arch="arm64" ;;
                *)            _arch="x64" ;;
            esac
            local _os_tag _ext
            # .tar.gz on both: .tar.xz needs xz-utils, which minimal images
            # (e.g. ubuntu:24.04) do not ship - tar then fails and, under
            # `set -e`, took down the whole installer at an optional stage.
            case "$OS" in
                macos) _os_tag="darwin"; _ext="tar.gz" ;;
                linux) _os_tag="linux";  _ext="tar.gz" ;;
            esac
            local _url="https://nodejs.org/dist/v$NODE_VERSION/node-v$NODE_VERSION-$_os_tag-$_arch.$_ext"
            local _tmp="/tmp/node-v$NODE_VERSION.$$_tarball"
            if _download_file "$_url" "$_tmp" "$T_NODE_DL"; then
                mkdir -p "$AGENT8088_HOME/node"
                # Guarded: Node is optional (WhatsApp bridge only), so a bad
                # tarball or missing decompressor must warn, not abort the run.
                tar -xf "$_tmp" -C "$AGENT8088_HOME/node" --strip-components=1 2>/dev/null || true
                rm -f "$_tmp"
                if [ -x "$AGENT8088_HOME/node/bin/node" ]; then
                    export PATH="$AGENT8088_HOME/node/bin:$PATH"
                    local _ver
                    _ver="$("$AGENT8088_HOME/node/bin/node" --version 2>/dev/null | sed 's/^v//')"
                    log_success "Node $_ver installed to $AGENT8088_HOME/node (portable, user-scoped)"
                    _node_ok=true
                fi
            fi
        fi

        if [ "$_node_ok" = false ]; then
            log_warn "Could not install Node $NODE_VERSION automatically."
            log_info "WhatsApp bridge needs Node 20.11+ - install manually from https://nodejs.org/"
            return 0
        fi
    fi

    NODE_INSTALLED=true

    # --- 2. npm install in the WhatsApp bridge dir ------------------------
    local _bridge_dir="$INSTALL_DIR/src/agent8088/gateway/platforms/whatsapp_bridge"
    if [ ! -f "$_bridge_dir/package.json" ]; then
        log_warn "WhatsApp bridge package.json not found at $_bridge_dir - skipping npm install"
        return 0
    fi
    if [ -d "$_bridge_dir/node_modules" ]; then
        log_success "WhatsApp bridge node_modules already present"
        WHATSAPP_BRIDGE_READY=true
        return 0
    fi

    log_info "Installing WhatsApp bridge npm dependencies..."
    _npm_rc=0
    run_with_timeout "$T_NPM" npm install --prefix "$_bridge_dir" --no-audit --no-fund \
        >/dev/null 2>&1 || _npm_rc=$?
    if [ "$_npm_rc" -eq 0 ]; then
        if [ -d "$_bridge_dir/node_modules" ]; then
            WHATSAPP_BRIDGE_READY=true
            log_success "WhatsApp bridge npm dependencies installed"
        else
            log_warn "WhatsApp bridge npm install reported success but node_modules missing"
            record_skip "WhatsApp bridge" "npm reported success but node_modules missing" \
                "npm install --prefix $_bridge_dir"
        fi
    else
        warn_stage "$_npm_rc" "$T_NPM" "WhatsApp bridge npm dependencies" \
            "WhatsApp adapter unavailable" \
            "npm install --prefix $_bridge_dir"
    fi
}

# ----------------------------------------------------------------------------
# Stage 5b2: Embedding model for persistent memory
# ----------------------------------------------------------------------------
# Memory is on by default, and its semantic recall needs an embedding model. This
# pulls it here rather than leaving it to first use, because the failure mode
# otherwise is silent: recall quietly degrades to keyword-only and the user has no
# reason to suspect the store is working at half strength.
#
# nomic-embed-text: 274 MB, 768 dimensions, 8192-token context. Chosen over the
# top-of-leaderboard qwen3-embedding:0.6b (~1.2 GB) because memories are one-line
# facts and short queries, and BM25 carries half the ranking through RRF - paying
# 4x the disk to sharpen a signal that is already cross-checked is the wrong
# trade. See docs/wiki/16-memory.md.
#
# Not fatal if it cannot be pulled: an install that dies because a 274 MB model
# download failed is worse than one that says memory will use keyword search
# until the model is there. The message names the exact command to fix it.
EMBED_MODEL="nomic-embed-text"

install_embedding_model() {
    if ! command -v ollama >/dev/null 2>&1; then
        # A cloud provider (OpenAI, Gemini, Cerebras...) serves /embeddings itself,
        # so there is nothing to pull. Only say something if memory would be worse
        # off, which is when Ollama is the configured provider.
        log_info "Ollama not found - memory will embed through your configured provider"
        return 0
    fi
    # `ollama list` talks to the daemon on :11434. It answers instantly when that
    # daemon is healthy and never when it is wedged, which is why it is guarded at
    # all despite being a local call.
    if run_with_timeout "$T_OLLAMA_CHECK" ollama list 2>/dev/null | grep -q "^${EMBED_MODEL}"; then
        log_success "Embedding model $EMBED_MODEL already present"
        return 0
    fi
    log_info "Pulling embedding model $EMBED_MODEL (274 MB, for memory recall)..."
    _pull_rc=0
    run_with_timeout "$T_OLLAMA_PULL" ollama pull "$EMBED_MODEL" >/dev/null 2>&1 || _pull_rc=$?
    if [ "$_pull_rc" -eq 0 ]; then
        log_success "Embedding model $EMBED_MODEL installed"
    else
        warn_stage "$_pull_rc" "$T_OLLAMA_PULL" "Embedding model $EMBED_MODEL" \
            "memory recall will use keyword search only" \
            "ollama pull $EMBED_MODEL"
    fi
}

# ----------------------------------------------------------------------------
# Stage 5c: Native sandbox runtime (Linux/macOS - auto-setup, Option B)
# ----------------------------------------------------------------------------
# install_native_sandbox() (engine.py:3344) needs Node+npm (installed by the
# prior stage), then runs `npm install @anthropic-ai/sandbox-runtime@<ver>`
# and checks for OS helper binaries: bwrap+socat+rg on Linux, rg on macOS
# (sandbox-exec ships with macOS). We install the OS helpers via the distro
# package manager (reusing the sudo_cmd pattern from check_git), then invoke
# `agent8088 --sandbox-setup` which runs engine.install_native_sandbox().
# On Windows this stage is skipped - the sandbox needs an elevated terminal
# (provisions a restricted account + WFP filter) which a user-scoped installer
# cannot guarantee. On Termux the bwrap/socat helpers are unreliable and the
# runtime is not validated there, so we skip it too.
install_native_sandbox() {
    # Only auto-run on fresh installs (not updates)
    [ "$FRESH_INSTALL" = true ] || { log_info "Existing installation updated - skipping sandbox setup"; return 0; }
    # Only Linux/macOS (Windows needs elevation, Termux helpers unreliable)
    case "$OS" in
        linux|macos) ;;
        *) return 0 ;;
    esac
    # Node is a hard prereq for the sandbox runtime
    [ "$NODE_INSTALLED" = true ] || { log_info "Node not available - native sandbox needs Node 20.11+. Skipping."; return 0; }

    local _shim
    _shim="$(get_command_link_dir)/agent8088"
    # The shim may not exist yet (setup_path runs after us). Build the path
    # the engine uses and invoke the venv python directly.
    local _py="$INSTALL_DIR/venv/bin/python"
    if [ ! -x "$_py" ]; then
        log_warn "venv python not found at $_py - skipping sandbox setup"
        record_skip "Native sandbox runtime" "venv python not found" \
            "agent8088 --sandbox-setup"
        return 0
    fi

    # Install OS helper binaries the runtime needs.
    case "$OS" in
        linux)
            local sudo_cmd=""
            [ "$(id -u 2>/dev/null || echo 1000)" -ne 0 ] && command -v sudo >/dev/null 2>&1 && sudo_cmd="sudo"
            # Native execution and its scheduled-task tool require these binaries.
            local _missing=()
            command -v bwrap >/dev/null 2>&1 || _missing+=("bubblewrap")  # Debian/Ubuntu pkg name
            command -v socat >/dev/null 2>&1 || _missing+=("socat")
            command -v rg >/dev/null 2>&1 || _missing+=("ripgrep")
            command -v crontab >/dev/null 2>&1 || _missing+=("crontab")
            if [ "${#_missing[@]}" -gt 0 ]; then
                log_info "Installing sandbox OS helpers: ${_missing[*]}..."
                case "$DISTRO" in
                    ubuntu|debian)
                        # Map package names for apt
                        local _apt_pkgs=()
                        for _pkg in "${_missing[@]}"; do
                            case "$_pkg" in
                                bubblewrap) _apt_pkgs+=("bubblewrap") ;;
                                socat)      _apt_pkgs+=("socat") ;;
                                ripgrep)    _apt_pkgs+=("ripgrep") ;;
                                crontab)    _apt_pkgs+=("cron") ;;
                            esac
                        done
                        $sudo_cmd env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${_apt_pkgs[@]}" >/dev/null 2>&1 || true
                        ;;
                    fedora)
                        local _dnf_pkgs=()
                        for _pkg in "${_missing[@]}"; do
                            case "$_pkg" in
                                bubblewrap) _dnf_pkgs+=("bubblewrap") ;;
                                socat)      _dnf_pkgs+=("socat") ;;
                                ripgrep)    _dnf_pkgs+=("ripgrep") ;;
                                crontab)    _dnf_pkgs+=("cronie") ;;
                            esac
                        done
                        $sudo_cmd dnf install -y "${_dnf_pkgs[@]}" >/dev/null 2>&1 || true
                        ;;
                    arch)
                        local _pacman_pkgs=()
                        for _pkg in "${_missing[@]}"; do
                            case "$_pkg" in
                                bubblewrap) _pacman_pkgs+=("bubblewrap") ;;
                                socat)      _pacman_pkgs+=("socat") ;;
                                ripgrep)    _pacman_pkgs+=("ripgrep") ;;
                                crontab)    _pacman_pkgs+=("cronie") ;;
                            esac
                        done
                        $sudo_cmd pacman -S --noconfirm "${_pacman_pkgs[@]}" >/dev/null 2>&1 || true
                        ;;
                esac
            fi
            ;;
        macos)
            # engine.py:3001 requires: sandbox-exec (ships with macOS), rg
            if ! command -v rg >/dev/null 2>&1; then
                if command -v brew >/dev/null 2>&1; then
                    log_info "Installing ripgrep via Homebrew..."
                    brew install ripgrep >/dev/null 2>&1 || true
                fi
            fi
            ;;
    esac

    # Invoke the engine's install_native_sandbox() via the CLI flag.
    log_info "Running native sandbox setup..."
    local _sandbox_setup
    if _sandbox_setup=$("$_py" -m agent8088.cli --sandbox-setup 2>&1); then
        SANDBOX_INSTALLED=true
        log_success "Native sandbox runtime installed and verified"
    else
        [ -n "$_sandbox_setup" ] && log_warn "$_sandbox_setup"
        log_warn "Native sandbox setup did not complete - Docker will be used automatically when available"
        record_skip "Native sandbox runtime" "setup did not complete" \
            "agent8088 --sandbox-setup   (Docker is used automatically meanwhile)"
    fi
}

# ----------------------------------------------------------------------------
# Stage 6: Link the command (shim + shell rc PATH edit)
# ----------------------------------------------------------------------------
get_command_link_dir() {
    if [ -n "${AGENT8088_LINK_DIR:-}" ]; then
        echo "$AGENT8088_LINK_DIR"
    elif is_termux && [ -n "${PREFIX:-}" ]; then
        echo "$PREFIX/bin"
    else
        echo "$HOME/.local/bin"
    fi
}

setup_path() {
    local link_dir
    link_dir="$(get_command_link_dir)"
    mkdir -p "$link_dir"

    # Write a shim (not a symlink) so we can unset inherited PYTHONPATH/PYTHONHOME
    # and avoid relying on `realpath` (missing on stock macOS).
    local shim="$link_dir/agent8088"
    cat > "$shim" <<EOF
#!/usr/bin/env bash
unset PYTHONPATH
unset PYTHONHOME
exec "$INSTALL_DIR/venv/bin/python" -m agent8088.cli "\$@"
EOF
    chmod +x "$shim"
    log_success "agent8088 command linked at $shim"

    # Edit shell rc files to add link_dir to PATH if not present.
    # macOS zsh on a clean install has no ~/.zshrc — touch it first.
    local rc_files=()
    case "$(basename "$SHELL")" in
        zsh)  rc_files=("$HOME/.zshrc" "$HOME/.zprofile") ;;
        bash) rc_files=("$HOME/.bashrc" "$HOME/.bash_profile" "$HOME/.profile") ;;
        fish) rc_files=() ;;  # fish_add_path handles it differently
        *)    rc_files=("$HOME/.profile") ;;
    esac
    local path_line="export PATH=\"$link_dir:\$PATH\""
    for rc in "${rc_files[@]}"; do
        [ -f "$rc" ] || touch "$rc"
        if ! grep -qF "$link_dir" "$rc" 2>/dev/null; then
            echo "$path_line" >> "$rc"
            log_info "Added $link_dir to PATH in $rc"
        fi
    done
    # fish
    if [ "$(basename "$SHELL")" = "fish" ] && command -v fish >/dev/null 2>&1; then
        fish -c "fish_add_path $link_dir" 2>/dev/null || true
    fi

    # Probe whether the command is now resolvable in a fresh login shell.
    # RHEL-family non-login root shells sometimes lose ~/.local/bin.
    if command -v bash >/dev/null 2>&1; then
        if ! bash -lc 'command -v agent8088' >/dev/null 2>&1; then
            if [ "$(id -u)" -eq 0 ] && [ "$OS" = "linux" ]; then
                log_warn "agent8088 not found in fresh login shell — writing PATH guard to ~/.bashrc"
                grep -qF "$link_dir" "$HOME/.bashrc" 2>/dev/null || echo "$path_line" >> "$HOME/.bashrc"
            fi
        fi
    fi
}

# ----------------------------------------------------------------------------
# Stage 7: Drop default config
# ----------------------------------------------------------------------------
drop_config() {
    if [ ! -f "$AGENT8088_HOME/config.txt" ]; then
        log_info "Dropping default config.txt to $AGENT8088_HOME/config.txt"
        # The default config.txt ships at src/agent8088/config.txt in the repo.
        # For an editable install (-e), site-packages only has a .pth pointer,
        # so the venv glob misses; the repo source path is the reliable one.
        local src_config="$INSTALL_DIR/venv/lib/python*/site-packages/agent8088/config.txt"
        local found=$(ls $src_config 2>/dev/null | head -1)
        if [ -n "$found" ] && [ -f "$found" ]; then
            cp "$found" "$AGENT8088_HOME/config.txt"
        elif [ -f "$INSTALL_DIR/config.txt" ]; then
            cp "$INSTALL_DIR/config.txt" "$AGENT8088_HOME/config.txt"
        elif [ -f "$INSTALL_DIR/src/agent8088/config.txt" ]; then
            cp "$INSTALL_DIR/src/agent8088/config.txt" "$AGENT8088_HOME/config.txt"
        else
            log_warn "No default config.txt found; you'll need to create one"
            return 0
        fi
        chmod 600 "$AGENT8088_HOME/config.txt"
        CONFIG_CREATED=true
        log_success "Default config.txt copied"
    else
        log_info "config.txt already exists at $AGENT8088_HOME/config.txt — preserving"
        chmod 600 "$AGENT8088_HOME/config.txt"
    fi

    # Set AGENT8088_CONFIG env var so the engine finds the user config.
    # Persist to shell rc files.
    local config_line="export AGENT8088_CONFIG=\"$AGENT8088_HOME/config.txt\""
    for rc in "$HOME/.zshrc" "$HOME/.bashrc" "$HOME/.bash_profile" "$HOME/.profile"; do
        [ -f "$rc" ] || continue
        if ! grep -qF "AGENT8088_CONFIG" "$rc" 2>/dev/null; then
            echo "$config_line" >> "$rc"
        fi
    done
    export AGENT8088_CONFIG="$AGENT8088_HOME/config.txt"
}

# ----------------------------------------------------------------------------
# Stage 9: Verify + finish
# ----------------------------------------------------------------------------
verify_install() {
    log_info "Verifying install..."
    local shim="$(get_command_link_dir)/agent8088"
    if [ -x "$shim" ]; then
        "$shim" --version 2>/dev/null && log_success "agent8088 is ready" || true
    fi
    echo ""
    echo -e "\033[0;32mDone.\033[0m  Run \033[1magent8088\033[0m to start."
    echo "  Config: $AGENT8088_HOME/config.txt"
    # Readiness summary - reflects what actually installed, not static text.
    if [ "$GATEWAY_EXTRAS_INSTALLED" = true ]; then
        echo "  Adapters: Slack/Discord/Telegram/WhatsApp (Python deps installed)"
    else
        echo "  Adapters: gateway extras not installed (run: uv pip install -e \".[gateway]\")"
    fi
    if [ "$SEARCH_EXTRAS_INSTALLED" = true ]; then
        echo "  Search:   keyless ddgs backend installed"
    else
        echo "  Search:   ddgs unavailable - configure SearXNG or an API-key backend"
    fi
    if [ "$CHROMIUM_INSTALLED" = true ]; then
        echo "  Browser:  Chromium installed (browse_page ready)"
    else
        echo "  Browser:  Chromium missing (browse_page will show install instructions)"
    fi
    if [ "$WHATSAPP_BRIDGE_READY" = true ]; then
        echo "  WhatsApp: Node bridge ready (run 'node bridge.js --pair' to pair)"
    elif [ "$NODE_INSTALLED" = true ]; then
        echo "  WhatsApp: Node installed but bridge npm deps missing"
    else
        echo "  WhatsApp: needs Node 20.11+ (install from https://nodejs.org/)"
    fi
    if [ "$SANDBOX_INSTALLED" = true ]; then
        echo "  Sandbox:  native runtime installed"
    else
        echo "  Sandbox:  Docker fallback is automatic when available"
        echo "            Native setup: agent8088 --sandbox-setup"
    fi
    echo "  Update: AGENT8088_BRANCH=$BRANCH curl -fsSL --proto '=https' --tlsv1.2 https://raw.githubusercontent.com/palindrome-rl/AGENT8088/$BRANCH/install.sh | bash"
    echo ""
    echo "If 'agent8088: command not found', open a NEW terminal (PATH was updated)."
    # Last, so it is the final thing on screen: per-stage warnings scrolled out of
    # view minutes ago on a multi-minute install, which is how a failed WhatsApp
    # bridge got reported and still went unnoticed.
    print_skipped_summary
}

run_agent8088_command() {
    if [ "$IS_INTERACTIVE" = false ] && (: </dev/tty) 2>/dev/null; then
        "$@" < /dev/tty
    else
        "$@"
    fi
}

# Runs on EVERY invocation, not only on a fresh install.
#
# The removed gate was `FRESH_INSTALL != true && CONFIG_CREATED != true`, which
# skipped setup on any re-run over an existing install that already had a config.
# That is exactly the run where setup matters most: when an optional stage fails the
# core agent still installs, so the user re-runs the installer -- and got no prompt
# for working directory, model or web search, and no hint that `agent8088 --setup`
# is the thing to run.
#
# Two gates remain, and both are there because the prompt is physically impossible,
# not because it is unwanted:
#   --skip-setup   an explicit request, honoured
#   no /dev/tty    nothing to read from; the message names the manual command
run_initial_setup() {
    if [ "$SKIP_SETUP" = true ]; then
        log_info "Skipping setup (--skip-setup)"
        log_info "Configure later with: agent8088 --setup"
        return 0
    fi
    if [ "$IS_INTERACTIVE" = false ] && ! (: </dev/tty) 2>/dev/null; then
        log_info "No TTY detected — skipping setup"
        log_info "Run agent8088 --setup later to configure your model."
        return 0
    fi

    # Prefer the shim, fall back to the venv interpreter. setup_path runs before us,
    # but a PATH-link directory that is not writable leaves no shim -- and skipping
    # setup because a symlink is missing, when the module is right there and
    # importable, is the wrong trade.
    local shim="$(get_command_link_dir)/agent8088"
    local venv_py="$INSTALL_DIR/venv/bin/python"
    local setup_cmd=()
    if [ -x "$shim" ]; then
        setup_cmd=("$shim" --setup)
    elif [ -x "$venv_py" ]; then
        log_warn "agent8088 shim not found; running setup via the venv interpreter"
        setup_cmd=("$venv_py" -m agent8088.cli --setup)
    else
        log_warn "agent8088 is not runnable yet; run agent8088 --setup later."
        record_skip "First-run setup" "agent8088 not runnable" "agent8088 --setup"
        return 0
    fi

    log_info "Starting setup..."
    if run_agent8088_command "${setup_cmd[@]}"; then
        INITIAL_SETUP_RAN=true
    else
        # Recorded, not just warned: on a multi-minute install this line scrolls out
        # of view, which is how a skipped setup went unnoticed.
        log_warn "Setup did not complete; run agent8088 --setup later."
        record_skip "First-run setup" "did not complete" "agent8088 --setup"
    fi
}

launch_initial_agent() {
    [ "$FRESH_INSTALL" = true ] || return 0
    [ "$INITIAL_SETUP_RAN" = true ] || return 0

    local shim="$(get_command_link_dir)/agent8088"
    echo ""
    log_info "Starting Agent8088..."
    run_agent8088_command "$shim"
}

# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
main() {
    print_banner
    detect_os
    install_uv
    check_python
    check_git
    clone_repo
    install_deps
    install_node_bridge
    install_embedding_model
    install_native_sandbox
    setup_path
    drop_config
    run_initial_setup
    verify_install
    launch_initial_agent
}

main "$@"
