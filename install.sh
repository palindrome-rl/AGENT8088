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
REPO_URL="https://github.com/tayyabimam1/AGENT8088.git"
REPO_BRANCH="${AGENT8088_BRANCH:-development}"
AGENT8088_HOME="${AGENT8088_HOME:-$HOME/.agent8088}"
INSTALL_DIR="$AGENT8088_HOME/agent8088"
PYTHON_VERSION="3.11"
PYTHON_FALLBACK_VERSIONS=("3.12" "3.10")

# Options
SKIP_SETUP=false
BRANCH="$REPO_BRANCH"
IS_INTERACTIVE=true

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
    fi
    local installed_commit
    installed_commit="$(git -C "$INSTALL_DIR" rev-parse --short HEAD 2>/dev/null || echo unknown)"
    log_success "Repository ready at $INSTALL_DIR ($BRANCH@$installed_commit)"
}

# ----------------------------------------------------------------------------
# Stage 5: Create venv + install the package
# ----------------------------------------------------------------------------
install_deps() {
    if [ "$DISTRO" = "termux" ]; then
        log_info "Creating venv (stdlib) and installing via pip..."
        python -m venv "$INSTALL_DIR/venv"
        # shellcheck disable=SC1091
        source "$INSTALL_DIR/venv/bin/activate"
        pip install --upgrade pip >/dev/null 2>&1
        pip install -e . >/dev/null 2>&1 || { log_error "pip install failed"; exit 1; }
    else
        log_info "Creating venv and installing via uv..."
        "$UV_CMD" venv "$INSTALL_DIR/venv" >/dev/null 2>&1
        "$UV_CMD" pip install --python "$INSTALL_DIR/venv/bin/python" -e "$INSTALL_DIR" >/dev/null 2>&1 || {
            log_error "uv pip install failed; trying with --all-extras"
            "$UV_CMD" pip install --python "$INSTALL_DIR/venv/bin/python" -e "$INSTALL_DIR" >/dev/null 2>&1 || {
                log_error "Failed to install agent8088"
                exit 1
            }
        }
    fi
    log_success "agent8088 installed (editable)"
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
        # The installed package ships a default config.txt next to engine.py.
        # Copy it from the venv's site-packages if available, else from repo.
        local src_config="$INSTALL_DIR/venv/lib/python*/site-packages/agent8088/config.txt"
        local found=$(ls $src_config 2>/dev/null | head -1)
        if [ -n "$found" ] && [ -f "$found" ]; then
            cp "$found" "$AGENT8088_HOME/config.txt"
        elif [ -f "$INSTALL_DIR/config.txt" ]; then
            cp "$INSTALL_DIR/config.txt" "$AGENT8088_HOME/config.txt"
        else
            log_warn "No default config.txt found; you'll need to create one"
            return 0
        fi
        log_success "Default config.txt copied"
    else
        log_info "config.txt already exists at $AGENT8088_HOME/config.txt — preserving"
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
    local current_paths=$(grep '^allowed_paths=' "$config" 2>/dev/null | cut -d= -f2- || echo "~")
    local new_paths
    if [ "$IS_INTERACTIVE" = true ]; then
        read -r -p "Working directory [$current_paths]: " new_paths || new_paths=""
    elif [ -r /dev/tty ] && [ -w /dev/tty ]; then
        printf "Working directory [%s]: " "$current_paths" > /dev/tty
        IFS= read -r new_paths < /dev/tty || new_paths=""
    fi
    new_paths="${new_paths:-$current_paths}"

    # provider name
    local current_provider=$(grep '^default_provider=' "$config" 2>/dev/null | cut -d= -f2- || echo "ollama")
    local new_provider
    if [ "$IS_INTERACTIVE" = true ]; then
        read -r -p "Provider name (ollama, openrouter, openai, groq, cerebras, etc.) [$current_provider]: " new_provider || new_provider=""
    elif [ -r /dev/tty ] && [ -w /dev/tty ]; then
        printf "Provider name [%s]: " "$current_provider" > /dev/tty
        IFS= read -r new_provider < /dev/tty || new_provider=""
    fi
    new_provider="${new_provider:-$current_provider}"

    # model name
    local current_model=$(grep "^provider\.${new_provider}\.model=" "$config" 2>/dev/null | cut -d= -f2- || echo "qwen14b-tooluse-v3")
    local new_model
    if [ "$IS_INTERACTIVE" = true ]; then
        read -r -p "Model name [$current_model]: " new_model || new_model=""
    elif [ -r /dev/tty ] && [ -w /dev/tty ]; then
        printf "Model name [%s]: " "$current_model" > /dev/tty
        IFS= read -r new_model < /dev/tty || new_model=""
    fi
    new_model="${new_model:-$current_model}"

    # api_key
    local current_key=$(grep "^provider\.${new_provider}\.api_key=" "$config" 2>/dev/null | cut -d= -f2- || echo "")
    local new_key
    if [ "$IS_INTERACTIVE" = true ]; then
        read -r -p "API key for $new_provider [press Enter to skip]: " new_key || new_key=""
    elif [ -r /dev/tty ] && [ -w /dev/tty ]; then
        printf "API key for %s [press Enter to skip]: " "$new_provider" > /dev/tty
        IFS= read -r new_key < /dev/tty || new_key=""
    fi

    # web search URL (optional)
    local current_search=$(grep '^search_base_url=' "$config" 2>/dev/null | cut -d= -f2- || echo "")
    local search_label="${current_search:-disabled}"
    local new_search
    if [ "$IS_INTERACTIVE" = true ]; then
        read -r -p "Web search URL (SearXNG) [$search_label]: " new_search || new_search=""
    elif [ -r /dev/tty ] && [ -w /dev/tty ]; then
        printf "Web search URL (SearXNG) [%s]: " "$search_label" > /dev/tty
        IFS= read -r new_search < /dev/tty || new_search=""
    fi

    # Resolve base_url from built-in providers
    local base_url=""
    case "$new_provider" in
        ollama)      base_url="http://localhost:11434/v1" ;;
        openrouter)  base_url="https://openrouter.ai/api/v1" ;;
        openai)      base_url="https://api.openai.com/v1" ;;
        anthropic)   base_url="https://api.anthropic.com/v1" ;;
        gemini)      base_url="https://generativelanguage.googleapis.com/v1beta/openai/" ;;
        cerebras)    base_url="https://api.cerebras.ai/v1" ;;
        deepseek)    base_url="https://api.deepseek.com/v1" ;;
        groq)        base_url="https://api.groq.com/openai/v1" ;;
        mistral)     base_url="https://api.mistral.ai/v1" ;;
        moonshot)    base_url="https://api.moonshot.ai/v1" ;;
        qwen)        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1" ;;
        ollama-cloud) base_url="https://ollama.com/v1" ;;
        copilot)     base_url="https://api.githubcopilot.com" ;;
        *)           base_url=$(grep "^provider\.${new_provider}\.base_url=" "$config" 2>/dev/null | cut -d= -f2- || echo "http://localhost:11434/v1") ;;
    esac

    # Write back
    sed -i.bak "s|^allowed_paths=.*|allowed_paths=$new_paths|" "$config"
    sed -i.bak "s|^default_provider=.*|default_provider=$new_provider|" "$config"
    grep -q "^default_provider=" "$config" || echo "default_provider=$new_provider" >> "$config"
    sed -i.bak "s|^provider\.${new_provider}\.base_url=.*|provider.${new_provider}.base_url=$base_url|" "$config"
    grep -q "^provider\.${new_provider}\.base_url=" "$config" || echo "provider.${new_provider}.base_url=$base_url" >> "$config"
    sed -i.bak "s|^provider\.${new_provider}\.model=.*|provider.${new_provider}.model=$new_model|" "$config"
    grep -q "^provider\.${new_provider}\.model=" "$config" || echo "provider.${new_provider}.model=$new_model" >> "$config"
    if [ -n "$new_key" ]; then
        sed -i.bak "s|^provider\.${new_provider}\.api_key=.*|provider.${new_provider}.api_key=$new_key|" "$config"
        grep -q "^provider\.${new_provider}\.api_key=" "$config" || echo "provider.${new_provider}.api_key=$new_key" >> "$config"
    fi
    if [ -n "$new_search" ]; then
        sed -i.bak "s|^#*\s*search_base_url=.*|search_base_url=$new_search|" "$config"
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
    echo "  Update: curl -fsSL https://raw.githubusercontent.com/tayyabimam1/AGENT8088/development/install.sh | bash"
    echo ""
    echo "If 'agent8088: command not found', open a NEW terminal (PATH was updated)."
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
    setup_path
    drop_config
    run_setup_wizard
    verify_install
}

main "$@"
