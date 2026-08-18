#!/bin/bash
# ============================================================================
# Agent8088 Installer — Linux, macOS, WSL2, Termux
# ============================================================================
# Usage:
#   curl -fsSL https://<YOUR-URL>/install.sh | bash
#
# Installs agent8088 as an isolated uv tool with a global `agent8088` command.
# Handles: uv bootstrap, Python provisioning, git install, repo clone, venv,
# editable install, PATH/shim, config drop, and a setup wizard.
# ============================================================================

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
EMBED_MODEL_READY=false
EMBED_VIA_PROVIDER=false

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
# Timeouts for the optional network stages
# ----------------------------------------------------------------------------
# Every optional stage already tolerates a *failure* (`|| true` + log_warn), but
# nothing protected them from a *hang*. A stalled `ollama pull`, an npm registry
# that accepts the connection and then goes quiet, or a wedged Ollama daemon left
# the installer waiting forever with no way out but Ctrl-C.
#
# Limits are deliberately moderate rather than maximally generous: every stage
# guarded here is optional and degrades to a "run this to fix it later" message,
# so the cost of cutting a slow-but-working download short is one rerun, while
# the cost of waiting too long is an installer that looks frozen. Roughly sized
# so a ~4 Mbps link finishes comfortably.
#
# Scale them all for a slow connection:
#   curl -fsSL <url> | AGENT8088_TIMEOUT_SCALE=3 bash
TIMEOUT_SCALE="${AGENT8088_TIMEOUT_SCALE:-1}"
case "$TIMEOUT_SCALE" in
    ''|*[!0-9]*) TIMEOUT_SCALE=1 ;;
esac
[ "$TIMEOUT_SCALE" -lt 1 ] && TIMEOUT_SCALE=1

T_OLLAMA_CHECK=$((15  * TIMEOUT_SCALE))   # local socket call - instant unless the daemon is wedged
T_OLLAMA_PULL=$((600  * TIMEOUT_SCALE))   # 274 MB embedding model
T_NPM=$((300          * TIMEOUT_SCALE))   # 142 small packages, mostly round-trips
T_CHROMIUM=$((600     * TIMEOUT_SCALE))   # ~150 MB browser download
T_NODE_DL=$((180      * TIMEOUT_SCALE))   # ~30 MB tarball
T_PIP=$((300          * TIMEOUT_SCALE))   # tens of MB of wheels

# Run a command under a wall-clock limit. macOS ships no `timeout` (it is GNU
# coreutils), so prefer timeout/gtimeout where they exist and fall back to a
# background watchdog that escalates TERM -> KILL.
#
# Returns 124 on timeout, matching GNU timeout, so callers can tell a hang from
# an ordinary failure. Must not let `wait` trip `set -e`.
run_with_timeout() {
    local _secs="$1"; shift
    local _rc=0

    if command -v timeout >/dev/null 2>&1; then
        timeout "$_secs" "$@" || _rc=$?
        return $_rc
    fi
    if command -v gtimeout >/dev/null 2>&1; then
        gtimeout "$_secs" "$@" || _rc=$?
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
        sleep 3
        kill -KILL "$_pid" 2>/dev/null || true
    ) >/dev/null 2>&1 &
    local _watchdog=$!

    wait "$_pid" 2>/dev/null || _rc=$?
    kill -KILL "$_watchdog" 2>/dev/null || true
    wait "$_watchdog" 2>/dev/null || true

    # A watchdog-killed child surfaces as 143 (TERM) or 137 (KILL); normalize both
    # to 124 so callers see one "timed out" code regardless of which path ran.
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

record_skip() {
    SKIPPED_STAGES+=("$1"$'\t'"$2"$'\t'"${3:-}")
}

# Warn about an optional stage that did not complete, naming a hang as a hang.
# "timed out after 10m" and "failed" point at different fixes. The optional 5th
# argument is the command that fixes it, surfaced again in the final summary.
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
                    . /etc/os-release
                    DISTRO="$ID"
                    DISTRO_VERSION="${VERSION_ID:-}"
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
            OS="unknown"; DISTRO="unknown"
            log_warn "Unknown operating system"
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
    if ! curl -LsSf https://astral.sh/uv/install.sh -o "$_uv_installer" 2>/dev/null; then
        log_error "Failed to download uv installer from https://astral.sh/uv/install.sh"
        log_info "Install manually: https://docs.astral.sh/uv/getting-started/installation/"
        rm -f "$_uv_installer"
        exit 1
    fi
    if UV_UNMANAGED_INSTALL="$AGENT8088_HOME/bin" sh "$_uv_installer" >/dev/null 2>&1; then
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
        git fetch --depth 1 origin "$BRANCH" >/dev/null 2>&1
        git checkout -B "$BRANCH" FETCH_HEAD >/dev/null 2>&1
        git reset --hard FETCH_HEAD >/dev/null 2>&1
    else
        log_info "Cloning Agent8088 repository..."
        rm -rf "$INSTALL_DIR"
        mkdir -p "$AGENT8088_HOME"
        git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
        cd "$INSTALL_DIR"
        git config core.autocrlf false
        FRESH_INSTALL=true
    fi
    local installed_commit
    installed_commit="$(git -C "$INSTALL_DIR" rev-parse --short HEAD 2>/dev/null || echo unknown)"
    log_success "Repository ready at $INSTALL_DIR ($BRANCH@$installed_commit)"
}

# ----------------------------------------------------------------------------
# Stage 5: Create venv + install the package
# ----------------------------------------------------------------------------
install_deps() {
    local _py="$INSTALL_DIR/venv/bin/python"
    if [ "$DISTRO" = "termux" ]; then
        log_info "Creating venv (stdlib) and installing via pip..."
        python -m venv "$INSTALL_DIR/venv"
        # shellcheck disable=SC1091
        source "$INSTALL_DIR/venv/bin/activate"
        pip install --upgrade pip >/dev/null 2>&1
        pip install --upgrade --force-reinstall -e . >/dev/null 2>&1 || { log_error "pip install failed"; exit 1; }
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
        if ! "$UV_CMD" venv --python "$PYTHON_PATH" --allow-existing "$INSTALL_DIR/venv" >/dev/null 2>&1 \
                || [ ! -x "$_py" ]; then
            # Reuse can legitimately fail: a venv built by a Python that has
            # since been removed or upgraded, or one left half-written by an
            # interrupted run. That is not something to hand back to the user as
            # a decision — rebuild it.
            log_warn "Existing virtualenv is not usable — rebuilding it"
            "$UV_CMD" venv --python "$PYTHON_PATH" --clear "$INSTALL_DIR/venv" >/dev/null 2>&1 || {
                log_error "Could not create the virtualenv at $INSTALL_DIR/venv"
                log_error "Run this to see the underlying error:"
                log_error "  $UV_CMD venv --python $PYTHON_PATH --clear $INSTALL_DIR/venv"
                log_error "If it keeps failing, remove the install and start clean:"
                log_error "  agent8088 --uninstall"
                exit 1
            }
        fi
        "$UV_CMD" pip install --python "$_py" --reinstall-package agent8088 -e "$INSTALL_DIR" >/dev/null 2>&1 || {
            log_error "uv pip install failed; trying with --all-extras"
            "$UV_CMD" pip install --python "$_py" --reinstall -e "$INSTALL_DIR" >/dev/null 2>&1 || {
                log_error "Failed to install agent8088"
                exit 1
            }
        }
    fi
    log_success "agent8088 installed (editable)"

    # --- Gateway adapter Python extras (Slack, Discord, WhatsApp, Telegram) ---
    # The [gateway] extra from pyproject.toml: slack-bolt, slack-sdk, httpx,
    # discord.py, python-telegram-bot. Without these, runner.py:463-497 guards
    # each adapter with try/except ImportError and silently no-ops.
    log_info "Installing gateway adapter dependencies (Slack, Discord, WhatsApp, Telegram)..."
    local _gw_rc=0
    if [ "$DISTRO" = "termux" ]; then
        run_with_timeout "$T_PIP" pip install -e ".[gateway]" >/dev/null 2>&1 || _gw_rc=$?
    else
        run_with_timeout "$T_PIP" "$UV_CMD" pip install --python "$_py" -e "$INSTALL_DIR[gateway]" >/dev/null 2>&1 || _gw_rc=$?
    fi
    if [ "$_gw_rc" -eq 0 ]; then
        GATEWAY_EXTRAS_INSTALLED=true
    else
        warn_stage "$_gw_rc" "$T_PIP" "Gateway adapters (Slack/Discord/Telegram)" \
            "core agent still works" \
            "$UV_CMD pip install --python $_py -e \"$INSTALL_DIR[gateway]\""
    fi
    [ "$GATEWAY_EXTRAS_INSTALLED" = true ] && log_success "Gateway adapters installed"

    # --- Keyless web search backend (optional [search] extra) ---
    # Installed everywhere so web_search keeps its no-key fallback; non-fatal
    # because ddgs->primp has no Android wheel and cannot build under Termux.
    log_info "Installing keyless web search backend (ddgs)..."
    local _search_rc=0
    if [ "$DISTRO" = "termux" ]; then
        run_with_timeout "$T_PIP" pip install -e ".[search]" >/dev/null 2>&1 || _search_rc=$?
    else
        run_with_timeout "$T_PIP" "$UV_CMD" pip install --python "$_py" -e "$INSTALL_DIR[search]" >/dev/null 2>&1 || _search_rc=$?
    fi
    if [ "$_search_rc" -eq 0 ]; then
        SEARCH_EXTRAS_INSTALLED=true
    else
        warn_stage "$_search_rc" "$T_PIP" "Keyless web search (ddgs)" \
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
    local _pw_rc=0
    if [ "$DISTRO" = "termux" ]; then
        run_with_timeout "$T_PIP" pip install -e ".[browser]" >/dev/null 2>&1 || _pw_rc=$?
    else
        run_with_timeout "$T_PIP" "$UV_CMD" pip install --python "$_py" -e "$INSTALL_DIR[browser]" >/dev/null 2>&1 || _pw_rc=$?
    fi
    if [ "$_pw_rc" -eq 0 ]; then
        _playwright_installed=true
    else
        warn_stage "$_pw_rc" "$T_PIP" "Playwright (browse_page)" \
            "browse_page will show install instructions" \
            "$UV_CMD pip install --python $_py -e \"$INSTALL_DIR[browser]\""
    fi
    if [ "$_playwright_installed" = true ]; then
        log_info "Installing Playwright Chromium browser (~280 MB)..."
        local _chromium_rc=0
        run_with_timeout "$T_CHROMIUM" "$_py" -m playwright install chromium >/dev/null 2>&1 || _chromium_rc=$?
        if [ "$_chromium_rc" -eq 0 ]; then
            CHROMIUM_INSTALLED=true
        else
            warn_stage "$_chromium_rc" "$T_CHROMIUM" "Chromium browser" \
                "browse_page will show install instructions" \
                "$_py -m playwright install chromium"
        fi
        if [ "$CHROMIUM_INSTALLED" = true ] && [ "$OS" = "linux" ]; then
            log_info "Installing Playwright Chromium system dependencies..."
            local _playwright_sudo=""
            [ "$(id -u 2>/dev/null || echo 1000)" -ne 0 ] && command -v sudo >/dev/null 2>&1 && _playwright_sudo="sudo"
            # Bounded because a sudo password prompt has nowhere to draw with output
            # redirected, so without a limit this waits on input that never comes.
            run_with_timeout "$T_PIP" $_playwright_sudo "$_py" -m playwright install-deps chromium >/dev/null 2>&1 || \
                log_warn "Playwright system dependencies were not installed - run: playwright install-deps chromium"
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
            if run_with_timeout "$T_NODE_DL" curl -fsSL "$_url" -o "$_tmp" 2>/dev/null; then
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
            record_skip "WhatsApp bridge (Node runtime)" "Node 20.11+ unavailable" \
                "install Node 20.11+ from https://nodejs.org/ then rerun this installer"
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
    # Run npm *from inside* the bridge directory rather than pointing --prefix at
    # it. npm 10 (bundled with Node 22.11) reads its config from --prefix but
    # still resolves package.json by walking up from the current directory, so
    # --prefix alone installs to the right place while failing to find anything
    # to install: ENOENT / errno -4058, "Could not read package.json".
    # The subshell keeps the directory change from leaking into later stages.
    local _npm_rc=0
    ( cd "$_bridge_dir" && run_with_timeout "$T_NPM" npm install --no-audit --no-fund ) \
        >/dev/null 2>&1 || _npm_rc=$?
    if [ "$_npm_rc" -eq 0 ]; then
        if [ -d "$_bridge_dir/node_modules" ]; then
            WHATSAPP_BRIDGE_READY=true
            log_success "WhatsApp bridge npm dependencies installed"
        else
            log_warn "WhatsApp bridge npm install reported success but node_modules missing"
            record_skip "WhatsApp bridge npm deps" "npm exited 0 but node_modules is missing" \
                "cd $_bridge_dir && npm install"
        fi
    else
        warn_stage "$_npm_rc" "$T_NPM" "WhatsApp bridge npm deps" \
            "the WhatsApp gateway will be unavailable until you rerun it" \
            "cd $_bridge_dir && npm install"
        log_warn "Fix it later with:  cd $_bridge_dir && npm install"
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
        EMBED_VIA_PROVIDER=true
        return 0
    fi
    # `ollama list` talks to the daemon on :11434. It answers instantly when that
    # daemon is healthy and never when it is wedged, so it needs a bound too -
    # otherwise the installer hangs here, before the download it was guarding.
    local _list_out=""
    _list_out="$(run_with_timeout "$T_OLLAMA_CHECK" ollama list 2>/dev/null || true)"
    if echo "$_list_out" | grep -q "^${EMBED_MODEL}"; then
        log_success "Embedding model $EMBED_MODEL already present"
        EMBED_MODEL_READY=true
        return 0
    fi

    log_info "Pulling embedding model $EMBED_MODEL (274 MB, for memory recall)..."
    local _pull_rc=0
    run_with_timeout "$T_OLLAMA_PULL" ollama pull "$EMBED_MODEL" >/dev/null 2>&1 || _pull_rc=$?
    if [ "$_pull_rc" -eq 0 ]; then
        log_success "Embedding model $EMBED_MODEL installed"
        EMBED_MODEL_READY=true
    else
        warn_stage "$_pull_rc" "$T_OLLAMA_PULL" "Embedding model ($EMBED_MODEL)" \
            "memory recall will use keyword search only" \
            "ollama pull $EMBED_MODEL"
        log_warn "Fix it later with:  ollama pull $EMBED_MODEL"
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
# Stage 8: Setup wizard
# ----------------------------------------------------------------------------
BUILTIN_MODEL_PROVIDERS=(
    ollama openrouter openai gemini cerebras deepseek groq mistral moonshot qwen ollama-cloud copilot
)

provider_label() {
    case "$1" in
        ollama)       echo "Ollama (local)" ;;
        openrouter)   echo "OpenRouter" ;;
        openai)       echo "OpenAI" ;;
        gemini)       echo "Google Gemini" ;;
        cerebras)     echo "Cerebras" ;;
        deepseek)     echo "DeepSeek" ;;
        groq)         echo "Groq" ;;
        mistral)      echo "Mistral" ;;
        moonshot)     echo "Moonshot (Kimi)" ;;
        qwen)         echo "Qwen (DashScope)" ;;
        ollama-cloud) echo "Ollama Cloud" ;;
        copilot)      echo "GitHub Copilot" ;;
        *)            echo "$1" ;;
    esac
}

provider_base_url() {
    case "$1" in
        ollama)       echo "http://localhost:11434/v1" ;;
        openrouter)   echo "https://openrouter.ai/api/v1" ;;
        openai)       echo "https://api.openai.com/v1" ;;
        gemini)       echo "https://generativelanguage.googleapis.com/v1beta/openai/" ;;
        cerebras)     echo "https://api.cerebras.ai/v1" ;;
        deepseek)     echo "https://api.deepseek.com/v1" ;;
        groq)         echo "https://api.groq.com/openai/v1" ;;
        mistral)      echo "https://api.mistral.ai/v1" ;;
        moonshot)     echo "https://api.moonshot.ai/v1" ;;
        qwen)         echo "https://dashscope.aliyuncs.com/compatible-mode/v1" ;;
        ollama-cloud) echo "https://ollama.com/v1" ;;
        copilot)      echo "https://api.githubcopilot.com" ;;
        *)            return 1 ;;
    esac
}

provider_default_model() {
    case "$1" in
        ollama)       echo "qwen14b-tooluse-v3" ;;
        openrouter)   echo "anthropic/claude-sonnet-4" ;;
        openai)       echo "gpt-4o" ;;
        gemini)       echo "gemini-2.0-flash" ;;
        cerebras)     echo "gpt-oss-120b" ;;
        deepseek)     echo "deepseek-chat" ;;
        groq)         echo "llama-3.3-70b-versatile" ;;
        mistral)      echo "mistral-small-latest" ;;
        moonshot)     echo "kimi-k2.6" ;;
        qwen)         echo "qwen-plus" ;;
        ollama-cloud) echo "gpt-oss:120b" ;;
        copilot)      echo "gpt-4o-mini" ;;
        *)            echo "model-name" ;;
    esac
}

is_builtin_provider() {
    local candidate="$1" provider
    for provider in "${BUILTIN_MODEL_PROVIDERS[@]}"; do
        [ "$candidate" = "$provider" ] && return 0
    done
    return 1
}

read_setup_value() {
    local prompt="$1" answer=""
    if [ "$IS_INTERACTIVE" = true ]; then
        read -r -p "$prompt" answer || answer=""
    elif (: </dev/tty) 2>/dev/null; then
        printf "%s" "$prompt" > /dev/tty
        IFS= read -r answer < /dev/tty || answer=""
    fi
    printf "%s" "$answer"
}

read_secret_setup_value() {
    local prompt="$1" answer=""
    if [ "$IS_INTERACTIVE" = true ]; then
        read -r -s -p "$prompt" answer || answer=""
        printf "\n" >&2
    elif (: </dev/tty) 2>/dev/null; then
        printf "%s" "$prompt" > /dev/tty
        IFS= read -r -s answer < /dev/tty || answer=""
        printf "\n" > /dev/tty
    fi
    printf "%s" "$answer"
}

select_model_provider() {
    local current_provider="$1" answer current_lower provider i
    echo "Select model provider:" >&2
    for i in "${!BUILTIN_MODEL_PROVIDERS[@]}"; do
        provider="${BUILTIN_MODEL_PROVIDERS[$i]}"
        printf "  %2d) %s (%s) - default: %s\n" "$((i + 1))" "$(provider_label "$provider")" "$provider" "$(provider_default_model "$provider")" >&2
    done
    printf "  %2d) %s\n" "$((${#BUILTIN_MODEL_PROVIDERS[@]} + 1))" "Custom OpenAI-compatible" >&2
    answer="$(read_setup_value "Choice [$current_provider]: ")"
    answer="${answer:-$current_provider}"
    if [[ "$answer" =~ ^[0-9]+$ ]]; then
        if [ "$answer" -ge 1 ] && [ "$answer" -le "${#BUILTIN_MODEL_PROVIDERS[@]}" ]; then
            printf "%s" "${BUILTIN_MODEL_PROVIDERS[$((answer - 1))]}"
            return 0
        fi
        if [ "$answer" -eq "$((${#BUILTIN_MODEL_PROVIDERS[@]} + 1))" ]; then
            printf "%s" "__custom__"
            return 0
        fi
    fi
    answer="$(printf "%s" "$answer" | tr '[:upper:]' '[:lower:]')"
    current_lower="$(printf "%s" "$current_provider" | tr '[:upper:]' '[:lower:]')"
    if is_builtin_provider "$answer"; then
        printf "%s" "$answer"
    elif [ "$answer" = "$current_lower" ]; then
        printf "%s" "$current_provider"
    elif [ "$answer" = "custom" ] || [ "$answer" = "custom openai-compatible" ] || [ "$answer" = "openai-compatible" ]; then
        printf "%s" "__custom__"
    else
        printf "Unknown provider '%s'; keeping %s\n" "$answer" "$current_provider" >&2
        printf "%s" "$current_provider"
    fi
}

run_setup_wizard() {
    if [ "$SKIP_SETUP" = true ]; then
        log_info "Skipping setup wizard (--skip-setup)"
        return 0
    fi

    # Auto-skip if no TTY (probe with (: </dev/tty) — Docker has the node but
    # opening it fails ENXIO).
    if [ "$IS_INTERACTIVE" = false ] && ! (: </dev/tty) 2>/dev/null; then
        log_info "No TTY detected — skipping setup wizard"
        log_info "Edit $AGENT8088_HOME/config.txt manually to configure your model."
        return 0
    fi

    local config="$AGENT8088_HOME/config.txt"
    log_info "Setup wizard"
    log_info "  (Press Enter to keep the default shown in brackets)"

    # working directory (allowed_paths)
    local current_paths
    current_paths="$(grep '^allowed_paths=' "$config" 2>/dev/null | cut -d= -f2- || true)"
    current_paths="${current_paths:-~}"
    local new_paths
    new_paths="$(read_setup_value "Working directory [$current_paths]: ")"
    new_paths="${new_paths:-$current_paths}"

    # provider picker
    local current_provider
    current_provider="$(grep '^default_provider=' "$config" 2>/dev/null | cut -d= -f2- || true)"
    current_provider="${current_provider:-ollama}"
    local selected_provider
    selected_provider="$(select_model_provider "$current_provider")"
    local new_provider="$selected_provider"
    local base_url=""
    if [ "$selected_provider" = "__custom__" ]; then
        local default_custom="custom"
        is_builtin_provider "$current_provider" || default_custom="$current_provider"
        new_provider="$(read_setup_value "Custom provider name [$default_custom]: ")"
        new_provider="${new_provider:-$default_custom}"
        if [[ ! "$new_provider" =~ ^[A-Za-z0-9_-]+$ ]]; then
            log_error "Custom provider names use letters, numbers, _ or -"
            exit 1
        fi
        local current_url
        current_url="$(grep "^provider\.${new_provider}\.base_url=" "$config" 2>/dev/null | cut -d= -f2- || true)"
        local url_label="required"
        [ -n "$current_url" ] && url_label="Enter keeps current"
        base_url="$(read_setup_value "OpenAI-compatible URL [$url_label]: ")"
        base_url="${base_url:-$current_url}"
        if [ -z "$base_url" ]; then
            log_error "OpenAI-compatible URL is required for custom providers"
            exit 1
        fi
    elif ! is_builtin_provider "$new_provider"; then
        base_url="$(grep "^provider\.${new_provider}\.base_url=" "$config" 2>/dev/null | cut -d= -f2- || true)"
        if [ -z "$base_url" ]; then
            log_error "OpenAI-compatible URL is required for custom providers"
            exit 1
        fi
    fi

    # model name
    local default_model
    default_model="$(provider_default_model "$new_provider")"
    local current_model
    current_model="$(grep "^provider\.${new_provider}\.model=" "$config" 2>/dev/null | cut -d= -f2- || true)"
    current_model="${current_model:-$default_model}"
    local new_model
    new_model="$(read_setup_value "Model name [$current_model]: ")"
    new_model="${new_model:-$current_model}"

    # api_key
    local current_key
    current_key="$(grep "^provider\.${new_provider}\.api_key=" "$config" 2>/dev/null | cut -d= -f2- || true)"
    local new_key
    new_key="$(read_secret_setup_value "API key for $new_provider [hidden; Enter keeps existing/skips]: ")"

    # web search URL (optional)
    local current_search
    current_search="$(grep '^search_base_url=' "$config" 2>/dev/null | cut -d= -f2- || true)"
    local new_search
    new_search="$(read_setup_value "Web search URL (SearXNG) [Enter keeps current; type none to disable]: ")"

    if [ -z "$base_url" ]; then
        base_url="$(provider_base_url "$new_provider" || true)"
    fi

    # Write back
    sed -i.bak "s|^allowed_paths=.*|allowed_paths=$new_paths|" "$config"
    local project_root="${new_paths%%,*}"
    project_root="$(printf "%s" "$project_root" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    if grep -q '^#*[[:space:]]*project_root=' "$config"; then
        sed -i.bak "s|^#*[[:space:]]*project_root=.*|project_root=$project_root|" "$config"
    else
        echo "project_root=$project_root" >> "$config"
    fi
    sed -i.bak "s|^default_provider=.*|default_provider=$new_provider|" "$config"
    grep -q "^default_provider=" "$config" || echo "default_provider=$new_provider" >> "$config"
    sed -i.bak "s|^provider\.${new_provider}\.base_url=.*|provider.${new_provider}.base_url=$base_url|" "$config"
    grep -q "^provider\.${new_provider}\.base_url=" "$config" || echo "provider.${new_provider}.base_url=$base_url" >> "$config"
    if ! is_builtin_provider "$new_provider"; then
        sed -i.bak "s|^provider\.${new_provider}\.api_mode=.*|provider.${new_provider}.api_mode=openai|" "$config"
        grep -q "^provider\.${new_provider}\.api_mode=" "$config" || echo "provider.${new_provider}.api_mode=openai" >> "$config"
    fi
    sed -i.bak "s|^provider\.${new_provider}\.model=.*|provider.${new_provider}.model=$new_model|" "$config"
    grep -q "^provider\.${new_provider}\.model=" "$config" || echo "provider.${new_provider}.model=$new_model" >> "$config"
    if [ -n "$new_key" ]; then
        sed -i.bak "s|^provider\.${new_provider}\.api_key=.*|provider.${new_provider}.api_key=$new_key|" "$config"
        grep -q "^provider\.${new_provider}\.api_key=" "$config" || echo "provider.${new_provider}.api_key=$new_key" >> "$config"
    fi
    # Anchored at column 0 so ONLY an active key is touched. config.txt documents
    # several commented `#   search_base_url=<example>` lines; a `^#*[[:space:]]*`
    # pattern matched every one of them and rewrote all four into duplicate active
    # keys, wiping the examples. No active line to replace is fine — the grep below
    # appends one.
    if [ "$(printf "%s" "$new_search" | tr '[:upper:]' '[:lower:]')" = "none" ]; then
        sed -i.bak '/^search_base_url=.*/d' "$config"
    elif [ -n "$new_search" ]; then
        sed -i.bak "s|^search_base_url=.*|search_base_url=$new_search|" "$config"
        grep -q "^search_base_url=" "$config" || echo "search_base_url=$new_search" >> "$config"
    fi
    rm -f "$config.bak"
    log_success "Config written to $config"
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
    # Memory: semantic recall needs an embedder; keyword search works without one.
    # No local Ollama is not a downgrade - the configured provider serves
    # /embeddings itself - so that case must not read as a failure.
    if [ "$EMBED_MODEL_READY" = true ]; then
        echo "  Memory:   $EMBED_MODEL ready (keyword + semantic recall)"
    elif [ "$EMBED_VIA_PROVIDER" = true ]; then
        echo "  Memory:   embeddings served by your configured provider"
    else
        echo "  Memory:   keyword recall only ($EMBED_MODEL not installed)"
    fi
    echo "  Update: AGENT8088_BRANCH=$BRANCH curl -fsSL https://raw.githubusercontent.com/palindrome-rl/AGENT8088/$BRANCH/install.sh | bash"
    echo ""
    echo "If 'agent8088: command not found', open a NEW terminal (PATH was updated)."
    print_skipped_summary
}

run_agent8088_command() {
    if [ "$IS_INTERACTIVE" = false ] && (: </dev/tty) 2>/dev/null; then
        "$@" < /dev/tty
    else
        "$@"
    fi
}

run_initial_setup() {
    if [ "$FRESH_INSTALL" != true ] && [ "$CONFIG_CREATED" != true ]; then
        log_info "Existing installation and config found — skipping first-run setup."
        return 0
    fi
    if [ "$SKIP_SETUP" = true ]; then
        log_info "Skipping first-run setup (--skip-setup)"
        return 0
    fi
    if [ "$IS_INTERACTIVE" = false ] && ! (: </dev/tty) 2>/dev/null; then
        log_info "No TTY detected — skipping first-run setup"
        log_info "Run agent8088 --setup later to configure your model."
        return 0
    fi

    local shim="$(get_command_link_dir)/agent8088"
    if [ ! -x "$shim" ]; then
        log_warn "agent8088 command is not ready yet; run agent8088 --setup later."
        return 0
    fi
    log_info "Starting first-run setup..."
    if run_agent8088_command "$shim" --setup; then
        INITIAL_SETUP_RAN=true
    else
        log_warn "First-run setup did not complete; run agent8088 --setup later."
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
