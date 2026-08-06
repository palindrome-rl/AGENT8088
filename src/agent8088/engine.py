#!/usr/bin/env python3
"""
Agent8088 - Clean CLI with banner + animated spinner.

A single shared agent loop (run_agent) drives both modes:
  - interactive REPL          (no args)
  - one-shot / benchmark mode (query as args, optional --trace)
"""
import ast, math, operator, signal, sys, subprocess, json, re, os, shlex, shutil, stat, tempfile, threading, time, uuid, atexit  # readline enables input history
try:
    import readline  # Unix-only; enables input history/editing
except ImportError:
    pass
from contextlib import nullcontext
from pathlib import Path
from openai import OpenAI
from agent8088.mcp import MCPRuntime

APP_DIR = Path(__file__).resolve().parent

import logging
_log = logging.getLogger("agent8088.engine")


# ---------------------------------------------------------------------------
# Config (simple key=value file)
# ---------------------------------------------------------------------------
def _protect_private_file(path: Path) -> None:
    if sys.platform != "win32":
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        return

    import csv

    identity = subprocess.run(
        ["whoami", "/user", "/fo", "csv", "/nh"],
        capture_output=True, text=True, timeout=10,
    )
    try:
        sid = next(csv.reader([identity.stdout]))[1]
    except (IndexError, StopIteration):
        sid = ""
    if identity.returncode or not re.fullmatch(r"S-\d(?:-\d+)+", sid):
        raise OSError("Could not determine the current Windows user SID.")
    for acl_args in (
        ["/grant:r", f"*{sid}:(R,W)"],
        ["/inheritance:r"],
    ):
        result = subprocess.run(
            ["icacls", str(path), *acl_args],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode:
            raise OSError(f"Could not protect private file: {path}")


def _write_private_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            _protect_private_file(temporary)
            stream.write(content)
        os.replace(temporary, path)
    except Exception:
        if temporary:
            temporary.unlink(missing_ok=True)
        raise


def load_simple_config(path: Path) -> dict:
    config = {}
    if not path.exists():
        return config
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        config[key.strip()] = value.strip()
    return config


def update_simple_config(path: Path, values: dict) -> None:
    """Update key=value settings while preserving the rest of the config file."""
    path = Path(path)
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    for key, raw_value in values.items():
        value = str(raw_value)
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", key) or "\n" in value or "\r" in value:
            raise ValueError(f"Invalid config value for {key!r}")
        line = f"{key}={value}"
        pattern = rf"^{re.escape(key)}=.*$"
        if re.search(pattern, content, re.MULTILINE):
            content = re.sub(pattern, lambda _: line, content, flags=re.MULTILINE)
        else:
            if content and not content.endswith("\n"):
                content += "\n"
            content += line + "\n"
    _write_private_text(path, content)



# --- .env key store ---

def load_env_file(path: Path = None) -> dict:
    """Load a .env file into a dict. Same format as load_simple_config."""
    if path is None:
        path = ENV_FILE_PATH if 'ENV_FILE_PATH' in globals() else Path.home() / ".agent8088" / ".env"
    if not path.exists():
        return {}
    env = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip()
    return env


def update_env_file(path: Path, values: dict) -> None:
    """Update key=value settings in a .env file with 0600 perms."""
    path = Path(path)
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    for key, raw_value in values.items():
        value = str(raw_value)
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", key) or "\n" in value or "\r" in value:
            raise ValueError(f"Invalid env value for {key!r}")
        line = f"{key}={value}"
        pattern = rf"^{re.escape(key)}=.*$"
        if re.search(pattern, content, re.MULTILINE):
            content = re.sub(pattern, lambda _: line, content, flags=re.MULTILINE)
        else:
            if content and not content.endswith("\n"):
                content += "\n"
            content += line + "\n"
    _write_private_text(path, content)


def _mask_value(value: str) -> str:
    """Mask a secret for display: sk-...cdef or (set, too short)."""
    if not value:
        return "(not set yet)"
    if len(value) < 8:
        return "(set, too short to mask)"
    return value[:3] + "..." + value[-4:]


def get_secret(config: dict, key: str, env_var: str = None) -> str:
    """Resolve a secret: .env file first, then config, then os.environ.
    If env_var is not given, derive it from key.upper()."""
    env_var = env_var or key.upper()
    _env = load_env_file()
    if env_var in _env:
        return _env[env_var]
    if os.environ.get(env_var):
        return os.environ[env_var]
    env_key = f"{key}_env"
    if env_key in config:
        env_name = config[env_key]
        if env_name in _env:
            return _env[env_name]
        if os.environ.get(env_name):
            return os.environ[env_name]
    return config.get(key, "")


def _migrate_keys_to_env(config_path: Path, env_path: Path) -> int:
    """One-time migration: move provider.*.api_key and *_token from config.txt to .env.
    Returns the number of keys migrated."""
    if env_path.exists():
        return 0  # already migrated
    config = load_simple_config(config_path)
    env_values = {}
    config_updates = {}
    migrated = 0

    for key, value in list(config.items()):
        if key.startswith("provider.") and key.endswith(".api_key") and value:
            provider_name = key.split(".")[1]
            env_var = f"{provider_name.upper().replace('-', '_')}_API_KEY"
            env_values[env_var] = value
            config_updates[f"provider.{provider_name}.api_key_env"] = env_var
            config_updates[key] = ""  # clear the literal key
            migrated += 1
        elif key.endswith("_bot_token") and value:
            env_var = key.upper()
            env_values[env_var] = value
            config_updates[f"{key}_env"] = env_var
            config_updates[key] = ""
            migrated += 1
        elif key.endswith("_app_token") and value:
            env_var = key.upper()
            env_values[env_var] = value
            config_updates[f"{key}_env"] = env_var
            config_updates[key] = ""
            migrated += 1

    if not migrated:
        return 0

    update_env_file(env_path, env_values)
    # Remove the literal keys from config.txt
    content = config_path.read_text(encoding="utf-8")
    for key in config_updates:
        if config_updates[key] == "":
            content = re.sub(rf"^{re.escape(key)}=.*\n?", "", content, flags=re.MULTILINE)
        else:
            line = f"{key}={config_updates[key]}"
            pattern = rf"^{re.escape(key)}=.*$"
            if re.search(pattern, content, re.MULTILINE):
                content = re.sub(pattern, lambda _: line, content, flags=re.MULTILINE)
            else:
                content += line + "\n"
    _write_private_text(config_path, content)
    return migrated


# Config path: AGENT8088_CONFIG env var > ~/.agent8088/config.txt > %LOCALAPPDATA%/agent8088/config.txt > APP_DIR/config.txt
_user_config = Path.home() / ".agent8088" / "config.txt"
_win_config = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "agent8088" / "config.txt"
if os.environ.get("AGENT8088_CONFIG"):
    CONFIG_PATH = Path(os.environ["AGENT8088_CONFIG"]).expanduser()
elif _user_config.exists():
    CONFIG_PATH = _user_config
elif _win_config.exists():
    CONFIG_PATH = _win_config
else:
    CONFIG_PATH = Path(str(APP_DIR / "config.txt")).expanduser()
APP_CONFIG = load_simple_config(CONFIG_PATH)

# .env key store lives next to config.txt
ENV_FILE_PATH = Path(str(CONFIG_PATH.parent / ".env"))

# One-time migration: move provider.*.api_key and *_token from config.txt to .env
try:
    _migrated_count = _migrate_keys_to_env(CONFIG_PATH, ENV_FILE_PATH)
    if _migrated_count:
        print(f"[agent8088] Migrated {_migrated_count} keys to {ENV_FILE_PATH}")
        APP_CONFIG = load_simple_config(CONFIG_PATH)
except Exception as _e:
    import logging as _logging
    _logging.getLogger("agent8088").debug("key migration skipped: %s", _e)

PROJECT_ROOT = Path(APP_CONFIG.get("project_root", os.getcwd())).expanduser().resolve()
sys.path.insert(0, str(PROJECT_ROOT))

# Ends at "q=" with NO placeholder — tools.txt appends {query_q} itself. (A trailing
# {query} here would produce a doubled placeholder in the final URL.)
SEARCH_BASE_URL = APP_CONFIG.get("search_base_url", "http://127.0.0.1:8888/search?q=")
GEMMA_BASE_URL = APP_CONFIG.get("gemma_base_url", "http://localhost:8003/v1")
TOOLS_FILE = Path(APP_CONFIG.get("tools_file", str(APP_DIR / "tools.txt"))).expanduser()
SHELL_CWD = Path(APP_CONFIG.get("shell_cwd", os.getcwd())).expanduser().resolve()
BANNER_FILE = Path(APP_CONFIG.get("banner_file", str(APP_DIR / "banner.txt"))).expanduser()
SYSTEM_FILE = Path(APP_CONFIG.get("system_file", str(APP_DIR / "system.md"))).expanduser()

MODEL_BASE_URL = APP_CONFIG.get("model_base_url", os.environ.get("OLLAMA_URL", "http://localhost:11434/v1"))
MODEL_NAME = APP_CONFIG.get("model_name", os.environ.get("MODEL_NAME", "qwen14b-tooluse-v3"))
TIMEOUT_SECONDS = int(APP_CONFIG.get("timeout_seconds", os.environ.get("TIMEOUT_SECONDS", "120")))
CONTEXT_WINDOW = int(APP_CONFIG.get("context_window", "32768"))
MAX_TOOL_OUTPUT_BYTES = int(APP_CONFIG.get("max_tool_output_bytes", str(1024 * 1024)))
MAX_READ_BYTES = int(APP_CONFIG.get("max_read_bytes", str(2 * 1024 * 1024)))
MAX_IMAGE_BYTES = int(APP_CONFIG.get("max_image_bytes", str(20 * 1024 * 1024)))
MAX_HTTP_BYTES = int(APP_CONFIG.get("max_http_bytes", str(5 * 1024 * 1024)))
SANDBOX_BACKEND = os.environ.get(
    "AGENT8088_SANDBOX", APP_CONFIG.get("sandbox_backend", "auto")
).strip().lower()
SANDBOX_RUNTIME_VERSION = APP_CONFIG.get("sandbox_runtime_version", "0.0.67")
SANDBOX_ALLOWED_DOMAINS = [
    value.strip()
    for value in APP_CONFIG.get("sandbox_allowed_domains", "").split(",")
    if value.strip()
]

# Tool templates interpolate from APP_CONFIG, so any default that a tool URL or
# command references must exist there too. Without this, a missing config key left
# `{search_base_url}` literal in the URL and web_search failed with the confusing
# "Blocked: scheme '' is not allowed" from the SSRF guard.
APP_CONFIG.setdefault("search_base_url", SEARCH_BASE_URL)
APP_CONFIG.setdefault("gemma_base_url", GEMMA_BASE_URL)
APP_CONFIG.setdefault("model_base_url", MODEL_BASE_URL)
APP_CONFIG.setdefault("model_name", MODEL_NAME)
APP_CONFIG.setdefault("project_root", str(PROJECT_ROOT))

# Anti-repetition sampling. Small local models can spiral into "I will not use any X…"
# loops; these penalties curb that. Default 0.0 = no-op (behaviour unchanged) — raise
# frequency_penalty to ~0.4 in config.txt to suppress repetition. Only sent when non-zero,
# so backends that don't support them are unaffected unless you opt in.
FREQUENCY_PENALTY = float(APP_CONFIG.get("frequency_penalty", "0"))
PRESENCE_PENALTY = float(APP_CONFIG.get("presence_penalty", "0"))

def _resolve_allowed_path(raw: str) -> Path:
    """Relative allowed_paths entries resolve against PROJECT_ROOT (the repo), not
    the shell's CWD — so `allowed_paths=.,/tmp` means the same thing no matter
    where the agent is launched from."""
    p = Path(raw).expanduser()
    return p.resolve() if p.is_absolute() else (PROJECT_ROOT / p).resolve()


ALLOWED_PATHS = [
    _resolve_allowed_path(p.strip())
    for p in APP_CONFIG.get("allowed_paths", str(PROJECT_ROOT)).split(",")
    if p.strip()
]

# ---------------------------------------------------------------------------
# Permission layer ÔÇö readonly by default, escalates to edit on user approval
# ---------------------------------------------------------------------------
PERMISSION_MODE = os.environ.get("AGENT8088_PERMISSION", "readonly")
_one_shot_grant = False  # set True by grant_escalation(), cleared after one blocked tool runs
_local_fallback_grant = False
_remote_git_grant = False
_plan_on_step = None        # set by CLI do_chat so _exec_plan can render the checklist
_plan_on_escalation = None  # set by CLI do_chat so _exec_plan escalations reach _handle_escalation
_plan_execution_grant = False  # temporary: set True when user approves a plan; cleared after plan completes

# ---------------------------------------------------------------------------
# Layer 1: Sensitive file read protection ÔÇö hardcoded blocklist + config override
# ---------------------------------------------------------------------------
SENSITIVE_FILE_PATTERNS = [
    ".env", "config.txt", "configb.txt", "id_rsa", "id_ed25519",
    ".ssh", ".gnupg", ".aws", ".gitconfig",
]
SENSITIVE_FILE_EXTENSIONS = frozenset([".pem", ".key", ".rsa", ".p12"])
SENSITIVE_FILE_GLOBS = ["*_KEY*", "*_SECRET*", "*_TOKEN*", "*_PASSWORD*",
                        "*_key*", "*_secret*", "*_token*", "*_password*"]

# Shell startup files: writing one is arbitrary code execution on the user's
# next shell launch, so writes are refused at the always-on floor — even in
# full-auto and even after an approved one-shot escalation. Reads stay allowed
# (matched on exact filename, so "profile.json" and ".editorconfig" are
# unaffected) because inspecting a dotfile is a normal, safe request.
SHELL_STARTUP_FILES = frozenset([
    ".bashrc", ".bash_profile", ".bash_login", ".bash_logout",
    ".zshrc", ".zshenv", ".zprofile", ".zlogin", ".zlogout",
    ".profile", ".login", ".cshrc", ".tcshrc", ".kshrc",
    "config.fish", "fish.config",
])


def _is_shell_startup_file(filepath: str) -> bool:
    """True if the path's filename is a shell startup file (write-blocked)."""
    return Path(filepath).name.lower() in SHELL_STARTUP_FILES

ALLOWED_SENSITIVE_FILES = set(
    p.strip() for p in APP_CONFIG.get("allowed_sensitive_files", "").split(",") if p.strip()
)


def _is_sensitive_path(filepath: str) -> bool:
    """Check if a file path matches the sensitive blocklist. Returns True if blocked."""
    fn = Path(filepath).name.lower()
    fp = str(filepath).lower()

    # Config override ÔÇö user explicitly allowed this file
    for allowed in ALLOWED_SENSITIVE_FILES:
        if allowed.lower() in fn or allowed.lower() in fp:
            return False

    # Exact filename match
    for pattern in SENSITIVE_FILE_PATTERNS:
        if pattern.lower() in fn or pattern.lower() in fp:
            return True

    # Extension match
    for ext in SENSITIVE_FILE_EXTENSIONS:
        if fn.endswith(ext):
            return True

    # Glob patterns
    import fnmatch
    for glob in SENSITIVE_FILE_GLOBS:
        if fnmatch.fnmatch(fn, glob):
            return True

    return False


# ---------------------------------------------------------------------------
# Layer 3: Path-based write restrictions ÔÇö three-tier zones
# ---------------------------------------------------------------------------
def _resolve_path_list(config_key: str, default: str = "") -> list:
    """Parse a comma-separated path list from config, resolve each to an absolute Path."""
    raw = APP_CONFIG.get(config_key, default)
    if not raw.strip():
        return []
    return [_resolve_allowed_path(p.strip()) for p in raw.split(",") if p.strip()]

NO_PROMPT_PATHS = _resolve_path_list("no_prompt_paths")
PROMPT_PATHS = _resolve_path_list("prompt_paths", ".")
BLOCKED_PATHS = _resolve_path_list("blocked_paths")
READ_PATHS = _resolve_path_list("read_paths")  # optional: if set, reads outside these escalate


def _check_path_zone(target: Path) -> str:
    """Return 'blocked', 'no_prompt', 'prompt', or 'default' for a write target."""
    for base in BLOCKED_PATHS:
        if target == base or base in target.parents:
            return "blocked"
    for base in NO_PROMPT_PATHS:
        if target == base or base in target.parents:
            return "no_prompt"
    for base in PROMPT_PATHS:
        if target == base or base in target.parents:
            return "prompt"
    return "default"

# Shell commands that are safe in readonly mode (inspection only)
READONLY_SAFE_COMMANDS = frozenset([
    # Unix
    "ls", "cat", "grep", "head", "tail", "wc", "pwd", "whoami",
    "date", "uname", "df", "du", "free", "nproc", "uptime", "diff",
    # Windows
    "dir", "type", "findstr", "where", "hostname", "ver", "vol",
    "tasklist", "systeminfo",
])
# Config-extensible: merge user-supplied safe commands from config.txt
_extra_safe = APP_CONFIG.get("readonly_safe_commands", "")
if _extra_safe.strip():
    READONLY_SAFE_COMMANDS = READONLY_SAFE_COMMANDS | frozenset(
        c.strip().lower() for c in _extra_safe.split(",") if c.strip())

_SHELL_CONTROL_RE = re.compile(r"[|&;<>\n`]|\$\(")
_GIT_READ_COMMANDS = frozenset(["status", "diff", "log", "show"])
_GIT_BRANCH_FLAGS = frozenset([
    "-a", "--all", "-r", "--remotes", "-v", "-vv", "--verbose",
    "--list", "--show-current", "--color", "--no-color",
])
_LOCAL_FILE_READ_COMMANDS = frozenset([
    "cat", "grep", "head", "tail", "wc", "diff", "type", "findstr",
])
_NON_EXEC_GIT_TEXT_COMMANDS = frozenset(["echo", "printf", "grep", "findstr"])


def _shell_parts(command: str) -> list:
    if _SHELL_CONTROL_RE.search(command):
        return []
    try:
        return shlex.split(command, posix=sys.platform != "win32")
    except ValueError:
        return []


def _dangerous_git_args(tokens: list) -> bool:
    cursor = 0
    options_with_value = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}
    while cursor < len(tokens) and tokens[cursor].startswith("-"):
        option = tokens[cursor].split("=", 1)[0]
        cursor += 2 if option in options_with_value and "=" not in tokens[cursor] else 1
    if cursor >= len(tokens):
        return False
    action = tokens[cursor].lower()
    flags = [token.lower() for token in tokens[cursor + 1:]]
    return (
        action == "push"
        or (action == "reset" and "--hard" in flags)
        or (action == "branch" and any(flag in ("-d", "-D", "--delete") for flag in flags))
        or (action == "clean" and any("f" in flag.lstrip("-") for flag in flags if flag.startswith("-")))
        or action in ("restore",)
        or (action == "checkout" and (
            "--" in flags
            or any(flag in ("-f", "--force") for flag in flags)
            or any(not flag.startswith("-") for flag in flags)
        ))
        or (action == "stash" and any(flag in ("drop", "clear") for flag in flags))
    )


# --- User-defined deny rules (config: deny_commands) ---
# fnmatch globs matched case-insensitively against the whole command text.
# Checked at the hardline floor, before any mode or approval — no override.
_USER_DENY_GLOBS = [
    g.strip() for g in APP_CONFIG.get("deny_commands", "").split(",") if g.strip()
]


def _matches_user_deny(command: str) -> bool:
    if not _USER_DENY_GLOBS:
        return False
    import fnmatch
    lowered = command.lower()
    return any(fnmatch.fnmatch(lowered, g.lower()) for g in _USER_DENY_GLOBS)


# --- Unrecoverable command floor (always-on, no override) ---
# Catastrophic commands that are blocked in ALL permission modes, including
# edit mode. These cause irreversible damage: filesystem wipes, disk formats,
# fork bombs, and remote-code-execution via pipe-to-shell at the root level.
_UNRECOVERABLE_PATTERNS = [
    # rm -rf / — wipe filesystem root (any flag order, --no-preserve-root, long/short)
    re.compile(r"\brm\s+(?:[^|;&<>]*\s)?-(?:[^-]*r|--recursive)(?:[^|;&<>]*\s)?(?:[^|;&<>]*\s)?/(?:\s|$)"),
    # rm -rf ~ — wipe home dir
    re.compile(r"\brm\s+(?:[^|;&<>]*\s)?-(?:[^-]*r|--recursive)(?:[^|;&<>]*\s)?~(?:\s|$)"),
    re.compile(r"\bmkfs(?:\.\w+)?\s+/dev/(?:sd[a-z]+|nvme\d+n\d+|vd[a-z]+|hd[a-z]+)"),
    re.compile(r"\bdd\s+if=\S+\s+of=/dev/(?:sd[a-z]+|nvme\d+n\d+|vd[a-z]+|hd[a-z]+)"),
    re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;"),
    # pipe remote content to shell (curl/wget ... | sh|bash)
    re.compile(r"\b(?:curl|wget)\b[^|;&<>]*\|\s*(?:sh|bash|dash|zsh|ksh)\b"),
    re.compile(r"\b(?:sh|bash|dash|zsh|ksh)\s*<\s*\(\s*(?:curl|wget)\s+"),
]


def _is_unrecoverable_command(command: str) -> bool:
    """Return True if the command matches an unrecoverable pattern.

    Checked before _hard_blocked_shell's git/wrapper logic so these patterns
    are caught even when the command is wrapped (bash -c 'rm -rf /') — the
    recursive _hard_blocked_shell call re-enters here for wrapped payloads.
    """
    for pattern in _UNRECOVERABLE_PATTERNS:
        if pattern.search(command):
            return True
    return False


def _git_read_targets_sensitive_file(command: str) -> bool:
    """Detect git read commands (show/diff/log) that target sensitive files.

    `git show HEAD:.env` and `git diff -- .env` bypass _is_sensitive_path
    because that check only runs in the read_text tool, not shell commands.
    Block these at the hardline floor so they're denied in ALL modes.
    """
    parts = _shell_parts(command)
    if not parts or len(parts) < 3:
        return False
    if Path(parts[0]).stem.lower() != "git":
        return False
    action = parts[1].lower()
    if action not in _GIT_READ_COMMANDS:
        return False
    # ponytail: scan non-flag tokens for sensitive paths.
    # `git show HEAD:.env` -> tokens after action: ["HEAD:.env"]
    # `git diff -- .env` -> tokens: ["--", ".env"]
    for token in parts[2:]:
        if token.startswith("-"):
            continue
        # `git show` uses `<rev>:<path>` form — extract the path part
        path_candidate = token.split(":", 1)[-1] if ":" in token else token
        if not path_candidate or path_candidate in ("--",):
            continue
        if _is_sensitive_path(path_candidate):
            return True
    return False


def _shell_targets_credential_path(command: str) -> bool:
    """Detect shell commands that write to or overwrite credential paths.

    `echo "x" > ~/.ssh/authorized_keys` and `cp file ~/.aws/credentials` bypass
    _is_sensitive_path because that check only runs in write_text/read_text tools.
    Block these at the hardline floor so credential writes are denied in ALL modes.
    """
    # ponytail: substring match on the raw command. Catches redirections
    # (>, >>), tee, cp/mv/install destinations. Ceiling: a command like
    # `cat ~/.ssh` (read, not write) would also match — acceptable, since
    # reading credentials via shell is also blocked by _is_sensitive_path
    # for the read_text tool and this is the shell equivalent.
    cred_markers = (
        ".ssh/", ".aws/", ".kube/", ".gnupg/", ".netrc",
        ".docker/", ".config/gcloud", "authorized_keys",
        "id_rsa", "id_ed25519", "id_ecdsa",
    )
    lowered = command.lower()
    return any(marker in lowered for marker in cred_markers)


def _hard_blocked_shell(command: str, _depth: int = 0) -> bool:
    if _is_unrecoverable_command(command):
        return True
    if _matches_user_deny(command):
        return True
    if _git_read_targets_sensitive_file(command):
        return True
    if _shell_targets_credential_path(command):
        return True
    try:
        lexer = shlex.shlex(command, posix=sys.platform != "win32",
                            punctuation_chars=";&|")
        lexer.whitespace_split = True
        lexer.commenters = ""
        parts = list(lexer)
    except ValueError:
        return False
    separators = {";", "&&", "||", "&", "|"}
    if _depth < 8:
        substitutions = re.findall(r"\$\(([^()]*)\)|`([^`]*)`", command)
        if any(_hard_blocked_shell(left or right, _depth + 1)
               for left, right in substitutions):
            return True
        if any(_hard_blocked_shell(payload, _depth + 1)
               for payload in re.findall(r"[<>]\(([^()]*)\)", command)):
            return True
        wrappers = {"sh", "bash", "dash", "zsh", "ksh", "fish", "cmd", "powershell", "pwsh"}
        command_flags = {"-c", "-lc", "/c", "-command"}
        for index, part in enumerate(parts):
            if Path(part).stem.lower() not in wrappers:
                continue
            end = next(
                (i for i in range(index + 1, len(parts)) if parts[i] in separators),
                len(parts),
            )
            for flag_index in range(index + 1, end - 1):
                if parts[flag_index].lower() in command_flags:
                    payload = " ".join(parts[flag_index + 1:end]).strip()
                    while (len(payload) >= 2 and payload[0] == payload[-1]
                           and payload[0] in ("'", '"')):
                        payload = payload[1:-1].strip()
                    if _hard_blocked_shell(payload, _depth + 1):
                        return True
                    break
    start = 0
    for end in range(len(parts) + 1):
        if end < len(parts) and parts[end] not in separators:
            continue
        segment = parts[start:end]
        start = end + 1
        if not segment:
            continue
        first = Path(segment[0]).stem.lower()
        if first in _NON_EXEC_GIT_TEXT_COMMANDS:
            continue
        for index, part in enumerate(segment):
            if Path(part).stem.lower() == "git" and _dangerous_git_args(segment[index + 1:]):
                return True
    return False


def _readonly_shell(command: str) -> bool:
    parts = _shell_parts(command)
    if not parts:
        return False
    executable = Path(parts[0]).stem.lower()
    if executable != "git":
        return executable in READONLY_SAFE_COMMANDS
    if len(parts) < 2:
        return False
    action = parts[1].lower()
    rest = parts[2:]
    if action == "branch":
        return all(
            part in _GIT_BRANCH_FLAGS or part.startswith("--color=")
            for part in rest
        )
    if action not in _GIT_READ_COMMANDS:
        return False
    unsafe_flags = ("--output", "--ext-diff", "--textconv")
    return not any(part == flag or part.startswith(flag + "=")
                   for part in rest for flag in unsafe_flags)


def _local_shell_reads_files(command: str) -> bool:
    parts = _shell_parts(command)
    if not parts:
        return False
    executable = Path(parts[0]).stem.lower()
    return (
        executable in _LOCAL_FILE_READ_COMMANDS
        or (executable == "git" and len(parts) > 1
            and parts[1].lower() in _GIT_READ_COMMANDS)
    )


def check_permission(mode: str, command: str = "", path_zone: str = "default",
                     host: bool = False) -> bool:
    """Return True if the tool mode is allowed in the current permission mode."""
    global _one_shot_grant
    if mode == "shell" and _hard_blocked_shell(command):
        return False
    if _plan_execution_grant and PERMISSION_MODE == "plan-only" and mode in ("write_text", "shell", "docker", "cron", "browser"):
        return True  # temporary grant for approved plan steps — only in plan-only mode
    if PERMISSION_MODE in ("edit", "full-auto"):
        return True
    if PERMISSION_MODE == "plan-only":
        if mode == "plan":
            return True
        if mode in ("read_text", "last_output", "python_eval"):
            return True
        if mode == "shell" and _readonly_shell(command):
            return True
        if mode == "cron" and command == "list":
            return True
        return False
    if mode == "write_text" and path_zone == "no_prompt":
        return True
    # readonly mode
    if mode in ("read_text", "last_output", "python_eval", "plan"):
        return True
    if mode == "cron" and command == "list":
        return True
    if mode == "read_text" and READ_PATHS:
        return False  # read_paths zone active: reads outside zone escalate
    if mode == "shell" and _readonly_shell(command):
        if ((host or _resolve_sandbox_backend() == "local")
                and _local_shell_reads_files(command)):
            return False
        return True
    # One-shot grant: allow one blocked tool through, then revert
    if _one_shot_grant:
        _one_shot_grant = False
        return True
    return False


def request_escalation(target_mode: str, paths: list, change_type: str, reason: str) -> str:
    """Return a structured escalation request string for the model to relay
    to the user. The UI intercepts this and prompts the user for approval."""
    return (
        f"ESCALATION_REQUEST:{target_mode}:{change_type}:{','.join(paths)}:{reason}"
    )


def grant_escalation(change_type: str = ""):
    """Allow exactly one blocked tool call to run, then revert to readonly.
    The user is prompted for every write/mutation - no session-wide grants."""
    global _one_shot_grant, _local_fallback_grant, _remote_git_grant
    if change_type == "git_remote_write":
        _remote_git_grant = True
        _one_shot_grant = False
        _local_fallback_grant = False
        return
    _one_shot_grant = True
    _local_fallback_grant = change_type == "local_execution"
    _remote_git_grant = False

DEFAULT_SYSTEM_PROMPT = "You are Agent8088. Read full instructions from system.md."


def load_text(path: Path, fallback: str) -> str:
    try:
        if path.exists():
            content = path.read_text().strip()
            if content:
                return content
    except Exception:
        pass
    return fallback


BASE_SYSTEM_PROMPT = load_text(SYSTEM_FILE, DEFAULT_SYSTEM_PROMPT)


# ---------------------------------------------------------------------------
# Model client.  USE_GEMMA4=1 switches to the Gemma server on Colossus.
# ---------------------------------------------------------------------------
def _normalize_openai_base_url(url: str) -> str:
    url = str(url or "").strip().rstrip("/")
    suffix = "/chat/completions"
    return url[:-len(suffix)] if url.endswith(suffix) else url


def load_providers(config: dict, include_builtins: bool = False) -> dict:
    """Parse `provider.<name>.<field>` keys from config into a registry.
    Fields: model, api_mode, base_url, api_key, api_key_env. OpenAI mode needs a base URL;
    LiteLLM mode also supports native provider identifiers such as Anthropic and
    Gemini without one. Credentials should use api_key_env, not api_key."""
    provs = {}
    from agent8088.providers import BUILTIN_PROVIDERS
    if include_builtins:
        for name, info in BUILTIN_PROVIDERS.items():
            provs[name] = {
                key: value for key, value in info.items()
                if key in {"base_url", "api_key", "api_key_env", "native_tools"}
            }
            provs[name]["model"] = info["default_model"]
    for key, value in config.items():
        if not key.startswith("provider."):
            continue
        parts = key.split(".", 2)
        if len(parts) != 3:
            continue
        _, name, field = parts
        provs.setdefault(name, {})[field] = value

    # Seed built-in base_urls so providers work with just api_key + model in config
    for name, info in BUILTIN_PROVIDERS.items():
        if name in provs and "base_url" not in provs[name]:
            provs[name]["base_url"] = info["base_url"]
    for provider in provs.values():
        if "base_url" in provider:
            provider["base_url"] = _normalize_openai_base_url(provider["base_url"])

    return {
        n: p for n, p in provs.items()
        if p.get("base_url") or (p.get("api_mode", "").lower() == "litellm" and p.get("model"))
    }


PROVIDERS = load_providers(APP_CONFIG, include_builtins=True)
DEFAULT_PROVIDER = APP_CONFIG.get("default_provider", "")
ACTIVE_PROVIDER = ""


def _provider_api_key(provider: dict) -> str:
    """Resolve a provider key, most explicit source first:

      1. the .env key store — where _migrate_keys_to_env puts secrets, so it is
         the canonical location and outranks a leftover plaintext api_key
      2. an explicit api_key in config.txt
      3. os.environ — ambient, so it is the LAST resort: a stray shell export
         (e.g. OPENAI_API_KEY set for another tool) must not silently redirect
         an explicitly configured provider
    """
    env_name = provider.get("api_key_env", "").strip()
    if env_name:
        _env = load_env_file()
        if env_name in _env:
            return _env[env_name]
    direct = provider.get("api_key", "").strip()
    if direct:
        return direct
    if env_name and os.environ.get(env_name):
        return os.environ[env_name]
    return ""


def get_client(provider: str = None):
    """Return (client, model_name) for a named provider.

    Precedence: explicit arg > AGENT8088_PROVIDER env > config default_provider >
    legacy USE_GEMMA4 toggle > the flat model_base_url/model_name settings."""
    name = (provider or os.environ.get("AGENT8088_PROVIDER") or DEFAULT_PROVIDER or "").strip()
    if name and name not in PROVIDERS and DEFAULT_PROVIDER in PROVIDERS:
        print(f"[agent8088] Unknown provider '{name}' — using {DEFAULT_PROVIDER}. "
              f"Known: {', '.join(sorted(PROVIDERS)) or '(none configured)'}")
        name = DEFAULT_PROVIDER

    if name and name in PROVIDERS:
        p = PROVIDERS[name]
        if p.get("api_mode", "openai").lower() == "litellm":
            return {
                "api_mode": "litellm",
                "api_base": p.get("base_url", ""),
                "api_key": _provider_api_key(p),
            }, p.get("model", MODEL_NAME)
        return OpenAI(base_url=p["base_url"],
                      api_key=_provider_api_key(p) or "none",
                      timeout=TIMEOUT_SECONDS), p.get("model", MODEL_NAME)

    if name:
        print(f"[agent8088] Unknown provider '{name}' — using legacy endpoint. "
              f"Known: {', '.join(sorted(PROVIDERS)) or '(none configured)'}")

    if os.environ.get("USE_GEMMA4", "0") == "1":  # legacy toggle, still supported
        print(f"[agent8088] Using Gemma 4 on Colossus ({GEMMA_BASE_URL})")
        model = APP_CONFIG.get("gemma_model_name", "gemma-4-12B-it-Q4_K_M.gguf")
        return OpenAI(base_url=GEMMA_BASE_URL, api_key="sk-dummy"), model

    client = OpenAI(base_url=MODEL_BASE_URL, api_key=APP_CONFIG.get("api_key", "ollama"), timeout=TIMEOUT_SECONDS)
    return client, MODEL_NAME


client, MODEL_NAME = get_client()
_initial_provider = (os.environ.get("AGENT8088_PROVIDER") or DEFAULT_PROVIDER or "").strip()
ACTIVE_PROVIDER = (_initial_provider if _initial_provider in PROVIDERS
                   else DEFAULT_PROVIDER if DEFAULT_PROVIDER in PROVIDERS else "")


def activate_model(provider: str = "", model: str = ""):
    """Select and persist a configured provider and optional model."""
    global client, MODEL_NAME, ACTIVE_PROVIDER, DEFAULT_PROVIDER
    if provider:
        if provider not in PROVIDERS:
            raise ValueError(f"Unknown provider: {provider}")
        next_client, default_model = get_client(provider)
        selected_model = (model or default_model).strip()
        if not selected_model:
            raise ValueError("A model is required")
        settings = {
            "default_provider": provider,
            f"provider.{provider}.model": selected_model,
        }
        for field in ("api_mode", "base_url", "api_key", "api_key_env", "native_tools"):
            value = PROVIDERS[provider].get(field)
            if value:
                settings[f"provider.{provider}.{field}"] = value
        update_simple_config(CONFIG_PATH, settings)
        APP_CONFIG.update(settings)
        PROVIDERS[provider]["model"] = selected_model
        client = next_client
        ACTIVE_PROVIDER = provider
        DEFAULT_PROVIDER = provider
        MODEL_NAME = selected_model
    elif model:
        selected_model = model.strip()
        if not selected_model:
            raise ValueError("A model is required")
        update_simple_config(CONFIG_PATH, {"model_name": selected_model})
        APP_CONFIG["model_name"] = selected_model
        MODEL_NAME = selected_model
    return client, MODEL_NAME


def _native_tools_enabled(tools, provider_name: str = "") -> bool:
    if not tools:
        return False
    provider = PROVIDERS.get(provider_name or ACTIVE_PROVIDER or DEFAULT_PROVIDER, {})
    value = provider.get("native_tools", False)
    return value if isinstance(value, bool) else str(value).lower() in ("1", "true", "yes", "on")


def _raise_if_interrupted(interrupt_check, stream=None):
    if not interrupt_check or not interrupt_check():
        return
    close = getattr(stream, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass
    raise AgentInterrupted()


def _start_interrupt_watcher(stream, interrupt_check):
    if not interrupt_check:
        return None, None
    stop = threading.Event()

    def watch():
        while not stop.wait(0.05):
            try:
                interrupted = interrupt_check()
            except Exception:
                return
            if interrupted:
                close = getattr(stream, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:
                        pass
                return

    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    return stop, watcher


def _finish_interrupt_watcher(stop, watcher):
    if stop:
        stop.set()
    if watcher:
        watcher.join(timeout=0.2)


def create_completion(client, messages, tools, max_tokens=2000, system_prompt=None,
                      temperature=0.1, on_token=None, interrupt_check=None,
                      model_name: str = "", provider_name: str = ""):
    selected_model = model_name or MODEL_NAME
    full_messages = [{"role": "system", "content": system_prompt or SYSTEM_PROMPT}, *messages]
    penalties = {}
    if FREQUENCY_PENALTY:
        penalties["frequency_penalty"] = FREQUENCY_PENALTY
    if PRESENCE_PENALTY:
        penalties["presence_penalty"] = PRESENCE_PENALTY
    if isinstance(client, dict) and client.get("api_mode") == "litellm":
        try:
            from litellm import completion
        except ImportError as e:
            raise RuntimeError("LiteLLM provider selected; run `pip install litellm`.") from e
        kwargs = {
            "model": selected_model, "messages": full_messages, "max_tokens": max_tokens,
            "temperature": temperature, "stream": on_token is not None, **penalties,
        }
        if _native_tools_enabled(tools, provider_name):
            kwargs["tools"] = tools
        if client.get("api_base"):
            kwargs["api_base"] = client["api_base"]
        if client.get("api_key"):
            kwargs["api_key"] = client["api_key"]
        _raise_if_interrupted(interrupt_check)
        response = completion(**kwargs)
        if on_token is None:
            return response
        collected, tool_chunks = [], {}
        stop, watcher = _start_interrupt_watcher(response, interrupt_check)
        try:
            for chunk in response:
                _raise_if_interrupted(interrupt_check, response)
                delta = chunk.choices[0].delta
                reasoning = getattr(delta, "reasoning_content", None)
                if reasoning:
                    on_token("reasoning", reasoning)
                if delta.content:
                    on_token("content", delta.content)
                    collected.append(delta.content)
                _collect_stream_tool_calls(delta, tool_chunks)
                _raise_if_interrupted(interrupt_check, response)
            _raise_if_interrupted(interrupt_check, response)
        except Exception:
            _raise_if_interrupted(interrupt_check, response)
            raise
        finally:
            _finish_interrupt_watcher(stop, watcher)
        return _build_response("".join(collected), tool_chunks)
    request_options = dict(
        model=selected_model, messages=full_messages, max_tokens=max_tokens,
        temperature=temperature, **penalties,
    )
    if _native_tools_enabled(tools, provider_name):
        request_options["tools"] = tools
    _raise_if_interrupted(interrupt_check)
    if on_token is None:
        return client.chat.completions.create(**request_options)
    # Streaming path — Rich UI passes on_token for live token-by-token rendering
    stream = client.chat.completions.create(**request_options, stream=True)
    collected, tool_chunks = [], {}
    stop, watcher = _start_interrupt_watcher(stream, interrupt_check)
    try:
        for chunk in stream:
            _raise_if_interrupted(interrupt_check, stream)
            delta = chunk.choices[0].delta
            rc = getattr(delta, "reasoning_content", None)
            if rc:
                on_token("reasoning", rc)
            if delta.content:
                on_token("content", delta.content)
                collected.append(delta.content)
            _collect_stream_tool_calls(delta, tool_chunks)
            _raise_if_interrupted(interrupt_check, stream)
        _raise_if_interrupted(interrupt_check, stream)
    except Exception:
        _raise_if_interrupted(interrupt_check, stream)
        raise
    finally:
        _finish_interrupt_watcher(stop, watcher)
    return _build_response("".join(collected), tool_chunks)


def _fallback_targets() -> list:
    targets = []
    for item in str(APP_CONFIG.get("fallback_models", "")).split(","):
        provider_name, separator, model_name = item.strip().partition(":")
        if separator and provider_name in PROVIDERS and model_name.strip():
            targets.append((provider_name, model_name.strip()))
    return targets


def _retryable_model_error(error: Exception) -> bool:
    status = getattr(error, "status_code", None)
    if status == 429 or isinstance(status, int) and status >= 500:
        return True
    name = type(error).__name__.lower()
    text = str(error).lower()
    retryable = (
        "timeout", "timed out", "connection", "rate limit", "temporarily unavailable",
        "service unavailable", "bad gateway", "gateway timeout",
    )
    return any(marker in name or marker in text for marker in retryable)


def _create_completion_with_fallback(messages, tools, *, temperature, system_prompt,
                                     on_token, interrupt_check, trace, turn):
    emitted = False

    def tracked_token(kind, delta):
        nonlocal emitted
        emitted = True
        if on_token:
            on_token(kind, delta)

    token_handler = tracked_token if on_token else None
    try:
        return create_completion(
            client, messages, tools, temperature=temperature,
            system_prompt=system_prompt, on_token=token_handler,
            interrupt_check=interrupt_check,
            provider_name=ACTIVE_PROVIDER or DEFAULT_PROVIDER,
        )
    except AgentInterrupted:
        raise
    except Exception as primary_error:
        if emitted or not _retryable_model_error(primary_error):
            raise
        last_error = primary_error

    for provider_name, model_name in _fallback_targets():
        if provider_name == (ACTIVE_PROVIDER or DEFAULT_PROVIDER) and model_name == MODEL_NAME:
            continue
        try:
            fallback_client, _ = get_client(provider_name)
            if trace is not None:
                trace.append({
                    "turn": turn,
                    "type": "model_fallback",
                    "provider": provider_name,
                    "model": model_name,
                })
            return create_completion(
                fallback_client, messages, tools, temperature=temperature,
                system_prompt=system_prompt, on_token=token_handler,
                interrupt_check=interrupt_check, model_name=model_name,
                provider_name=provider_name,
            )
        except AgentInterrupted:
            raise
        except Exception as fallback_error:
            last_error = fallback_error
            if emitted:
                raise
    raise last_error


def _collect_stream_tool_calls(delta, chunks):
    for tool_call in getattr(delta, "tool_calls", None) or []:
        index = getattr(tool_call, "index", 0)
        entry = chunks.setdefault(index, {"id": "", "name": "", "arguments": ""})
        entry["id"] += getattr(tool_call, "id", None) or ""
        function = getattr(tool_call, "function", None)
        if function:
            entry["name"] += getattr(function, "name", None) or ""
            entry["arguments"] += getattr(function, "arguments", None) or ""


def _build_response(content, tool_chunks=None):
    """Reconstruct a ChatCompletion-like object from streamed content
    so run_agent() can read .choices[0].message.content uniformly."""
    tool_calls = []
    for index in sorted(tool_chunks or {}):
        call = tool_chunks[index]
        function = type("F", (), {
            "name": call["name"], "arguments": call["arguments"] or "{}",
        })()
        tool_calls.append(type("T", (), {
            "id": call["id"] or f"call_{index}", "type": "function", "function": function,
        })())
    return type("R", (), {"choices": [type("C", (), {
        "message": type("M", (), {"content": content, "tool_calls": tool_calls}),
        "finish_reason": "tool_calls" if tool_calls else "stop",
    })()]})


def _native_tool_text(message) -> str:
    lines = []
    for tool_call in getattr(message, "tool_calls", None) or []:
        function = getattr(tool_call, "function", None)
        if not function or not getattr(function, "name", ""):
            continue
        arguments = getattr(function, "arguments", None) or "{}"
        try:
            arguments = json.dumps(json.loads(arguments))
        except (TypeError, json.JSONDecodeError):
            arguments = "{}"
        lines.append(f"✿FUNCTION✿: {function.name} ✿ARGS✿: {arguments}")
    return "\n".join(lines)


_IMAGE_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".gif": "image/gif", ".webp": "image/webp"}


def build_image_message(text: str, images: list) -> dict:
    """Build a multimodal user message: text plus one or more images.
    Local paths are inlined as base64 data URLs; http(s) URLs pass through
    (SSRF-checked). Requires a vision-capable model/provider."""
    import base64 as _b64
    parts = [{"type": "text", "text": text or ""}]
    for ref in images or []:
        ref = str(ref).strip()
        if ref.startswith(("http://", "https://")):
            blocked = _ssrf_check(ref)
            if blocked:
                raise ValueError(blocked)
            parts.append({"type": "image_url", "image_url": {"url": ref}})
            continue
        path = resolve_user_path(ref)
        if not path.exists():
            raise ValueError(f"Image not found: {path}")
        if _is_sensitive_path(str(path)):
            raise ValueError(f"Access to sensitive file denied: {path}")
        mime = _IMAGE_MIME.get(path.suffix.lower())
        if not mime:
            raise ValueError(f"Unsupported image type: {path.suffix or '(none)'}")
        if path.stat().st_size > MAX_IMAGE_BYTES:
            raise ValueError(f"Image is too large (limit: {MAX_IMAGE_BYTES} bytes): {path}")
        b64 = _b64.b64encode(path.read_bytes()).decode()
        parts.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
    return {"role": "user", "content": parts}


class AgentInterrupted(Exception):
    """Raised when the user interrupts the agent loop (e.g. ESC in the Rich UI)."""
    pass


# ---------------------------------------------------------------------------
# Tool specs (loaded from tools.txt, with config.txt as fallback)
# ---------------------------------------------------------------------------
def default_tool_description(name: str) -> str:
    return name.replace("_", " ").strip().capitalize()


def parse_csv(raw: str) -> list:
    return [x.strip() for x in (raw or "").split(",") if x.strip()]


def parse_kv_segments(segments: list) -> dict:
    out = {}
    for seg in segments:
        seg = seg.strip()
        if seg and "=" in seg:
            k, v = seg.split("=", 1)
            out[k.strip().lower()] = v.strip()
    return out


def _build_spec(name: str, extra: dict, config: dict, description: str) -> dict:
    # Each field prefers the inline tools.txt value, then config.txt, then a default.
    def g(ekey, ckey, default=""):
        return extra.get(ekey, config.get(f"{ckey}.{name}", default))
    return {
        "name": name,
        "description": description,
        "mode": (extra.get("mode") or config.get(f"tool_mode.{name}") or "shell").strip().lower(),
        "args": parse_csv(g("args", "tool_params")),
        "keywords": set(parse_csv(g("keywords", "tool_keywords"))),
        "command": g("command", "tool_command"),
        "url": g("url", "tool_url"),
        # http_get/http_post extras. jq filters and JSON bodies are pipe- and
        # comma-heavy, which collides with tools.txt's '|' field separator — so
        # these are normally set in config.txt as tool_filter.<name> etc., where
        # the value is everything after the first '='.
        # host=1 runs a CURATED tool on the host instead of inside the sandbox.
        # Only for tools whose command is fixed or built as structured argv (no shell
        # interpolation of model input) and that need host binaries/credentials — the
        # git tools. Never set this on execute_shell, which takes arbitrary commands.
        "host": g("host", "tool_host"),
        "headers": g("headers", "tool_headers"),
        "body": g("body", "tool_body"),
        "filter": g("filter", "tool_filter"),
        "extract": g("extract", "tool_extract"),
        "expression": g("expression", "tool_expression"),
        "path_arg": g("path_arg", "tool_path_arg", "filename"),
        "content_arg": g("content_arg", "tool_content_arg", "content"),
        "timeout": int(g("timeout", "tool_timeout", "25")),
        "arg_types": _parse_arg_types(g("arg_types", "tool_arg_types")),
    }


def _parse_arg_types(raw: str) -> dict:
    """Parse 'steps:array,filename:string' into {'steps': 'array', 'filename': 'string'}."""
    result = {}
    for pair in (raw or "").split(","):
        pair = pair.strip()
        if ":" in pair:
            k, v = pair.split(":", 1)
            result[k.strip()] = v.strip()
    return result


def load_tool_specs(path: Path, config: dict) -> dict:
    specs = {}
    if path.exists():
        for raw in path.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split("|")]
            name = parts[0] if parts else ""
            if not name:
                continue
            desc = parts[1] if len(parts) > 1 and parts[1] else default_tool_description(name)
            extra = parse_kv_segments(parts[2:] if len(parts) > 2 else [])
            specs[name] = _build_spec(name, extra, config, desc)
    if not specs:  # fall back to a flat "tools=a,b,c" list in config
        for name in parse_csv(config.get("tools", "")):
            specs[name] = _build_spec(name, {}, config, default_tool_description(name))
    return specs


def build_tools_def(tool_specs: dict) -> list:
    result = []
    for name, spec in tool_specs.items():
        # MCP tools declare their own parameters schema; built-in tools use args + arg_types
        if "parameters" in spec:
            params = spec["parameters"]
        else:
            props = {}
            for param in spec["args"]:
                arg_types = spec.get("arg_types", {})
                props[param] = {"type": arg_types.get(param, "string")}
            params = {"type": "object", "properties": props,
                      "required": list(spec["args"])}
        result.append({
            "type": "function",
            "function": {
                "name": name,
                "description": spec["description"],
                "parameters": params,
            },
        })
    return result


TOOL_SPECS = load_tool_specs(TOOLS_FILE, APP_CONFIG)
MCP_RUNTIME = MCPRuntime(PROJECT_ROOT)
TOOL_SPECS.update(MCP_RUNTIME.reload(TOOL_SPECS))
TOOLS_DEF = build_tools_def(TOOL_SPECS)
TOOL_NAMES = set(TOOL_SPECS.keys())
TOOL_REQUIRED_PARAMS = {name: list(spec["args"]) for name, spec in TOOL_SPECS.items()}


def reload_mcp_tools():
    """Reconnect MCP servers and refresh their registered tools."""
    global TOOLS_DEF, TOOL_NAMES, TOOL_REQUIRED_PARAMS, SYSTEM_PROMPT
    for name, spec in list(TOOL_SPECS.items()):
        if spec.get("mode") == "mcp":
            TOOL_SPECS.pop(name)
    TOOL_SPECS.update(MCP_RUNTIME.reload(TOOL_SPECS))
    TOOLS_DEF = build_tools_def(TOOL_SPECS)
    TOOL_NAMES = set(TOOL_SPECS)
    TOOL_REQUIRED_PARAMS = {name: list(spec["args"]) for name, spec in TOOL_SPECS.items()}
    SYSTEM_PROMPT = BASE_SYSTEM_PROMPT + "\n" + render_tool_docs(TOOL_SPECS) + render_skill_docs(SKILL_PACKAGES) + render_persona(USER_FILE)
    return MCP_RUNTIME.statuses


atexit.register(MCP_RUNTIME.close)

TOOL_ALIASES = {
    "bash": "execute_shell", "sh": "execute_shell",
    "shell": "execute_shell", "run": "execute_shell",
    "search": "web_search", "web": "web_search", "google": "web_search",
    "read": "read_text", "cat": "read_text",
    "write": "write_file", "create_file": "write_file", "writefile": "write_file",
    "calc": "calculate", "eval": "calculate", "math": "calculate",
    "last": "last_output", "prev_output": "last_output",
}


def _resolve_tool_name(name):
    """Resolve a model-emitted tool name to its canonical name via alias map.
    Canonical names pass through unchanged; unknown names pass through too
    (so the TOOL_NAMES check fails naturally and the call is skipped)."""
    return TOOL_ALIASES.get(name, name)


def render_tool_docs(specs: dict) -> str:
    """Generate the tool section of the system prompt from TOOL_SPECS, so the
    prompt can never drift from tools.txt. Required because the Ollama backend
    rejects the OpenAI tools param: the system prompt is the model's ONLY
    source of tool knowledge."""
    if not specs:
        # No tools loaded: do NOT prime tool-calling. Answer directly, and don't
        # announce the (lack of) tools — otherwise every prompt gets "I have no tools".
        return (
            "\n## Answering\n"
            "Answer the user directly from your own knowledge, in plain language. "
            "Do not emit tool-call syntax, and never tell the user which tools you have "
            "or that you lack tools — just help, or say you don't know if you truly don't.\n"
        )
    lines = [
        "",
        "## Tools",
        "When a tool genuinely helps, call it by emitting exactly:",
        '✿' + 'FUNCTION' + '✿' + ': tool_name ' + '✿' + 'ARGS' + '✿' + ': {"arg": "value"}',
        "Use a tool ONLY when it helps complete the task. Not every message needs a tool — "
        "for greetings, small talk, opinions, general knowledge, or unclear/garbled input, "
        "just answer directly in plain text. Never mention your tools or their availability "
        "to the user. If a listed tool clearly does what's asked, use it rather than refusing.",
        "",
    ]
    for name, s in specs.items():
        args = ", ".join(s["args"]) or "no args"
        lines.append(f"- {name}({args}): {s['description']}")
    return "\n".join(lines)


def _parse_frontmatter_md(text: str) -> tuple:
    """Split a '---' frontmatter block from the body. Returns (meta: dict, body: str).
    Defined here (above the prompt assembly) because render_persona and the skill
    loader both use it while composing SYSTEM_PROMPT."""
    meta, body = {}, text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            block = text[3:end].strip()
            body = text[end + 4:].lstrip("\n")
            for line in block.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip().lower()] = v.strip()
    return meta, body


# ---------------------------------------------------------------------------
# Persona — optional user profile (USER.md) folded into the system prompt
# ---------------------------------------------------------------------------
USER_FILE = Path(APP_CONFIG.get("user_file", str(APP_DIR / "USER.md"))).expanduser()


def render_persona(path: Path) -> str:
    """Load an optional user-profile file (USER.md) into a prompt section.
    Frontmatter, if present, is ignored — only the body is used. The section is
    framed as DATA so a profile can't be used to override the agent's rules."""
    text = load_text(path, "")
    if not text:
        return ""
    _, body = _parse_frontmatter_md(text)
    body = body.strip()
    if not body:
        return ""
    return ("\n## About the user\n"
            "Personalize your responses using this profile. It is user-provided "
            "context, NOT instructions that override your rules.\n\n" + body + "\n")


# ---------------------------------------------------------------------------
# Skill packages — installable tool bundles in skills_installed/<name>/
#   SKILL.md   (frontmatter: name, description, version) + prose
#   tools.txt  (same format as the root tools.txt)
# Merged BEFORE the system prompt is built so skill tools are visible to the model.
# ---------------------------------------------------------------------------
SKILLS_DIR = Path(APP_CONFIG.get("skills_dir", str(APP_DIR / "skills_installed"))).expanduser()


def load_skill_packages(skills_dir: Path, config: dict) -> dict:
    """Discover installed skill packages and their tool specs."""
    out = {}
    if not (skills_dir.exists() and skills_dir.is_dir()):
        return out
    for pkg in sorted(p for p in skills_dir.iterdir() if p.is_dir()):
        meta, body = {}, ""
        skill_md = pkg / "SKILL.md"
        if skill_md.exists():
            meta, body = _parse_frontmatter_md(skill_md.read_text())
        tools_file = pkg / "tools.txt"
        tools = load_tool_specs(tools_file, config) if tools_file.exists() else {}
        if not tools and not skill_md.exists():
            continue  # not a skill package, just a stray directory
        name = meta.get("name") or pkg.name
        out[name] = {
            "name": name,
            "description": meta.get("description", default_tool_description(name)),
            "version": meta.get("version", "0"),
            "category": meta.get("category", "general"),
            "path": str(pkg),
            "prose": body.strip(),
            "tools": tools,
        }
    return out


def merge_skill_tools(core: dict, skills: dict) -> dict:
    """Merge skill-provided tools into the core set. Core tools ALWAYS win — an
    installed package must never be able to redefine execute_shell and friends."""
    merged = dict(core)
    for skill in skills.values():
        for tname, tspec in (skill.get("tools") or {}).items():
            if tname in merged:
                continue  # never override a core (or earlier skill's) tool
            merged[tname] = tspec
    return merged


def render_skill_docs(skills: dict) -> str:
    """Make installed skill playbooks available to the agent, not just the CLI UI."""
    if not skills:
        return ""
    lines = ["", "## Installed skills"]
    for name, skill in skills.items():
        prose = (skill.get("prose") or "").strip()
        lines.append(f"\n### {name}\n{skill['description']}")
        if prose:
            lines.append(prose)
    return "\n".join(lines)


SKILL_PACKAGES = load_skill_packages(SKILLS_DIR, APP_CONFIG)
if SKILL_PACKAGES:
    TOOL_SPECS = merge_skill_tools(TOOL_SPECS, SKILL_PACKAGES)
    TOOLS_DEF = build_tools_def(TOOL_SPECS)
    TOOL_NAMES = set(TOOL_SPECS.keys())
    TOOL_REQUIRED_PARAMS = {name: list(spec["args"]) for name, spec in TOOL_SPECS.items()}


SYSTEM_PROMPT = (BASE_SYSTEM_PROMPT + "\n" + render_tool_docs(TOOL_SPECS)
                 + render_skill_docs(SKILL_PACKAGES) + render_persona(USER_FILE))

_last_tool_output = ""
_last_tool_name = ""
_last_write_diff = []


# ---------------------------------------------------------------------------
# Subagents — profiles loaded from agents/*.md (frontmatter + body prompt)
# ---------------------------------------------------------------------------
AGENTS_DIR = Path(APP_CONFIG.get("agents_dir", str(APP_DIR / "agents"))).expanduser()
DEFAULT_SUBAGENT = APP_CONFIG.get("default_subagent", "general-purpose")
SUBAGENT_MAX_DEPTH = int(APP_CONFIG.get("subagent_max_depth", "1"))

_DEFAULT_SUBAGENT_PROFILE = {
    "name": "general-purpose",
    "description": "General-purpose sub-agent for multi-step research, search, and code tasks.",
    "tools": sorted(n for n in TOOL_NAMES if n != "spawn_subagent"),
    "max_turns": 8,
    "system_prompt": (
        "You are a focused sub-agent spawned to complete ONE delegated task with a "
        "fresh context. Use your tools actively. When done, reply with a concise final "
        "report of what you found or did — no preamble. Do not ask the caller questions."
    ),
}


def load_subagent_specs(agents_dir: Path) -> dict:
    specs = {}
    if agents_dir.exists() and agents_dir.is_dir():
        for path in sorted(agents_dir.glob("*.md")):
            meta, body = _parse_frontmatter_md(path.read_text())
            name = meta.get("name") or path.stem
            specs[name] = {
                "name": name,
                "description": meta.get("description", default_tool_description(name)),
                "tools": parse_csv(meta.get("tools", "")),
                "max_turns": int(meta.get("max_turns", "8")),
                "system_prompt": body.strip() or _DEFAULT_SUBAGENT_PROFILE["system_prompt"],
            }
    if DEFAULT_SUBAGENT not in specs:
        specs[DEFAULT_SUBAGENT] = dict(_DEFAULT_SUBAGENT_PROFILE, name=DEFAULT_SUBAGENT)
    return specs


SUBAGENT_SPECS = load_subagent_specs(AGENTS_DIR)

# UI hook: a presentation layer (e.g. the Rich CLI) may set this to a factory
#   subagent_ui(agent_type, task, depth) -> dict of run_agent hooks
# with any of the keys: spin, on_calls, on_tool, on_result, done(answer).
# Left None, sub-agents run silently (benchmark, one-shot, plain REPL) — so this
# is fully backward-compatible. Kept out of the loop, same as every other hook.
subagent_ui = None


# ---------------------------------------------------------------------------
# Tool execution engine
# ---------------------------------------------------------------------------
def resolve_user_path(raw_path: str) -> Path:
    p = Path(raw_path or "").expanduser()
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    resolved = p.resolve()
    if ALLOWED_PATHS and not any(resolved == base or base in resolved.parents for base in ALLOWED_PATHS):
        raise ValueError(f"Path not allowed: {resolved}")
    return resolved


def _read_text_limited(path: Path, limit: int = MAX_READ_BYTES) -> str:
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"File is too large to read (limit: {limit} bytes): {path}")
    return data.decode("utf-8")


def classify_plan_component(step_text: str) -> str:
    text = (step_text or "").lower()
    words = set(re.findall(r"[a-z0-9_]+", text))
    best_tool, best_score = None, -1
    for name, spec in TOOL_SPECS.items():
        if spec.get("mode") == "plan":
            continue
        score = sum(1 for part in name.lower().split("_") if part and part in text)
        score += len(words.intersection(spec.get("keywords", set())))
        if score > best_score:
            best_score, best_tool = score, name
    return best_tool or next(iter(TOOL_NAMES), "")


def _infer_step_args(tool_name: str, step_text: str, given_args: dict = None) -> dict:
    args = dict(given_args or {})
    required = TOOL_REQUIRED_PARAMS.get(tool_name, [])
    missing = [p for p in required if p not in args]
    if missing and len(required) == 1:
        args[required[0]] = step_text
    return args


def _exec_process(command, timeout: int = 25, shell: bool = False) -> str:
    kwargs = {
        "shell": shell,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "cwd": str(SHELL_CWD),
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
        if shell:
            kwargs["executable"] = shutil.which("bash") or "/bin/sh"
    output = bytearray()
    truncated = False

    with subprocess.Popen(command, **kwargs) as process:
        def drain():
            nonlocal truncated
            while True:
                chunk = process.stdout.read(65536)
                if not chunk:
                    break
                remaining = MAX_TOOL_OUTPUT_BYTES - len(output)
                if remaining > 0:
                    output.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    truncated = True

        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                    capture_output=True, timeout=10,
                )
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            reader.join(timeout=2)
            return f"Command timed out after {timeout}s."
        reader.join()
    text = output.decode(errors="replace").strip()
    if truncated:
        text += f"\n[output truncated at {MAX_TOOL_OUTPUT_BYTES} bytes]"
    if returncode:
        suffix = f"Command exited with status {returncode}."
        return f"{text}\n{suffix}".strip()
    return text or "Command completed."


def _exec_shell_command(command: str, timeout: int = 25) -> str:
    return _exec_sandbox_command(command, timeout=timeout)


def _process_display(argv: list) -> str:
    return subprocess.list2cmdline(argv) if sys.platform == "win32" else shlex.join(argv)


def _structured_tool_argv(name: str, args: dict):
    if name == "git_clone":
        url = str(args.get("url", ""))
        directory = str(args.get("directory", ""))
        if not url or any(char in url for char in ("\0", "\r", "\n")) or "::" in url:
            raise ValueError("git clone requires a safe repository URL.")
        if any(char in directory for char in ("\0", "\r", "\n")):
            raise ValueError("git clone requires a safe destination path.")
        return ["git", "clone", "--", url] + ([directory] if directory else [])
    if name == "git_commit":
        return ["git", "commit", "-m", str(args.get("message", ""))]
    if name == "git_push":
        return ["git", "push", "origin", "HEAD"]
    if name == "git_create_pr":
        return ["gh", "pr", "create", "--title", str(args.get("title", "")),
                "--body", str(args.get("body", ""))]
    return None


def _exec_structured_tool(name: str, args: dict, timeout: int) -> str:
    argv = _structured_tool_argv(name, args)
    on_host = bool((TOOL_SPECS.get(name) or {}).get("host"))
    runner = (lambda a: _exec_process(a, timeout=timeout)) if on_host else (
        lambda a: _exec_sandbox_argv(a, timeout=timeout))
    if name == "git_commit":
        staged = runner(["git", "add", "-A"])
        if "exited with status" in staged or "timed out" in staged:
            return staged
    return _missing_binary_hint(argv[0] if argv else "", runner(argv))


def _missing_binary_hint(program: str, result: str) -> str:
    """Turn a bare 'not found' / status 127 into an actionable message. Without this a
    missing binary inside the sandbox surfaced as an opaque `sh: 1: git: not found`."""
    if program and ("not found" in result or "status 127" in result):
        return (f"'{program}' is not available where this tool ran. "
                f"Install {program}, or set host=1 for this tool if it needs host binaries.")
    return result


_CALCULATOR_BINARY_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_CALCULATOR_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def _safe_calculate(expression: str):
    if len(expression) > 1000:
        raise ValueError("expression is too long")
    tree = ast.parse(expression, mode="eval")
    if sum(1 for _ in ast.walk(tree)) > 128:
        raise ValueError("expression is too complex")

    def evaluate(node):
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            value = node.value
        elif isinstance(node, ast.UnaryOp) and type(node.op) in _CALCULATOR_UNARY_OPS:
            value = _CALCULATOR_UNARY_OPS[type(node.op)](evaluate(node.operand))
        elif isinstance(node, ast.BinOp) and type(node.op) in _CALCULATOR_BINARY_OPS:
            left, right = evaluate(node.left), evaluate(node.right)
            if isinstance(node.op, ast.Pow):
                if abs(right) > 1000:
                    raise ValueError("exponent is too large")
                if (type(left) is int and type(right) is int and right >= 0
                        and abs(left) > 1 and left.bit_length() * right > 4096):
                    raise ValueError("result is too large")
            value = _CALCULATOR_BINARY_OPS[type(node.op)](left, right)
        else:
            raise ValueError("only arithmetic expressions are allowed")
        if type(value) is int and value.bit_length() > 4096:
            raise ValueError("result is too large")
        if type(value) is float and not math.isfinite(value):
            raise ValueError("result is not finite")
        return value

    return evaluate(tree.body)


def _format_with_args(template: str, args: dict) -> str:
    import urllib.parse
    # Config supplies defaults like {project_root}; model args override and win.
    safe = dict(APP_CONFIG)
    for k, v in args.items():
        sv = str(v)
        safe[k] = sv
        safe[f"{k}_q"] = urllib.parse.quote(sv)
    try:
        return (template or "").format(**safe)
    except KeyError as exc:
        raise ValueError(f"Missing required argument: {exc.args[0]}") from None


def _exec_plan(args: dict, on_step=None, on_escalation=None, depth: int = 0) -> str:
    raw = args.get("steps") or args.get("plan") or ""
    steps = raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            steps = parsed if isinstance(parsed, list) else [s.strip(" -") for s in raw.splitlines() if s.strip()]
        except Exception:
            steps = [s.strip(" -") for s in raw.splitlines() if s.strip()]
    if not isinstance(steps, list):
        return "Error: execute_plan requires a list of steps or newline plan text."
    if not steps:
        return ("Error: execute_plan requires a non-empty steps array. "
                'Pass steps as a JSON array, e.g.: [{"tool":"write_file",'
                '"arguments":{"filename":"C:/tmp/x.txt","content":"hi"}}]')

    total = len(steps)

    # In plan-only mode: show all steps as pending, then get approval BEFORE running.
    # On approve: set _plan_execution_grant so steps run without per-step prompts,
    # but PERMISSION_MODE stays plan-only (temporary grant, not a mode switch).
    global _plan_execution_grant
    if PERMISSION_MODE == "plan-only" and on_step and on_escalation:
        pre_parsed = []
        has_gated = False
        for idx, step in enumerate(steps, 1):
            if isinstance(step, dict):
                step_text = str(step.get("step") or step.get("text") or "")
                tool_name = str(step.get("tool") or classify_plan_component(step_text))
            else:
                step_text = str(step)
                tool_name = classify_plan_component(step_text)
            spec = TOOL_SPECS.get(tool_name, {})
            if spec.get("mode") in ("write_text", "shell", "docker", "cron", "browser"):
                has_gated = True
            pre_parsed.append((idx, step_text, tool_name))
        for idx, step_text, tool_name in pre_parsed:
            on_step(idx, total, step_text, tool_name, "pending", None)
        if has_gated:
            approved = on_escalation(
                request_escalation(
                    "edit", ["(plan)"], "plan_approval",
                    f"Plan has {total} step(s). Review the checklist above and approve to execute."
                )
            )
            if not approved:
                return "Plan denied — staying in plan-only mode."
            _plan_execution_grant = True

    outputs = []
    for idx, step in enumerate(steps, 1):
        if isinstance(step, dict):
            step_text = str(step.get("step") or step.get("text") or "")
            tool_name = str(step.get("tool") or classify_plan_component(step_text))
            given = step.get("arguments") if isinstance(step.get("arguments"), dict) else {}
            tool_args = _infer_step_args(tool_name, step_text, given)
        else:
            step_text = str(step)
            tool_name = classify_plan_component(step_text)
            tool_args = _infer_step_args(tool_name, step_text, {})
        if tool_name not in TOOL_SPECS:
            outputs.append(f"[{idx}] Error: unknown tool '{tool_name}'.")
            continue
        missing = [param for param in TOOL_REQUIRED_PARAMS.get(tool_name, [])
                   if param not in tool_args]
        if missing:
            result = (f"Error: plan step requires arguments for {tool_name}: "
                      f"{', '.join(missing)}. Use a JSON step with tool and arguments.")
            outputs.append(f"[{idx}] {tool_name}: {result}")
            if on_step:
                on_step(idx, total, step_text, tool_name, "done", result)
            continue
        if on_step:
            on_step(idx, total, step_text, tool_name, "running", None)
        try:
            result = run_tool(tool_name, tool_args, allow_plan=False, depth=depth)
        except Exception as exc:
            result = f"Error: {exc}"
        if result.startswith("ESCALATION_REQUEST:") and callable(on_escalation):
            if on_escalation(result):
                try:
                    result = run_tool(tool_name, tool_args, allow_plan=False, depth=depth)
                except Exception as exc:
                    result = f"Error: {exc}"
        if on_step:
            on_step(idx, total, step_text, tool_name, "done", result[:500])
        outputs.append(f"[{idx}] {tool_name}: {result[:500]}")
    _plan_execution_grant = False  # clear temporary grant — back to plan-only
    return "\n".join(outputs)


def _exec_subagent(args: dict, depth: int = 0) -> str:
    """Run a delegated task in a fresh, tool-restricted sub-agent loop.
    Bounded by SUBAGENT_MAX_DEPTH. Returns the sub-agent's final answer."""
    global _last_tool_output, _last_tool_name, _last_write_diff

    if depth >= SUBAGENT_MAX_DEPTH:
        return (f"Error: subagent recursion depth limit ({SUBAGENT_MAX_DEPTH}) reached. "
                "Complete the task yourself instead of delegating further.")

    task = str(args.get("task") or args.get("prompt") or args.get("instruction") or "").strip()
    if not task:
        return "Error: spawn_subagent requires a non-empty 'task'."

    type_name = str(args.get("agent_type") or args.get("type") or DEFAULT_SUBAGENT).strip()
    profile = SUBAGENT_SPECS.get(type_name)
    if profile is None:
        available = ", ".join(sorted(SUBAGENT_SPECS)) or "(none)"
        return f"Error: unknown agent_type '{type_name}'. Available: {available}."

    # Restrict to the profile's tools that actually exist; sub-agents never get
    # spawn_subagent (bounds recursion in addition to the depth guard).
    allowed = {n for n in profile["tools"] if n in TOOL_NAMES and n != "spawn_subagent"}
    if not allowed:  # empty/misconfigured profile -> give it the safe read-only default
        allowed = {n for n in ("read_text", "execute_shell", "web_search") if n in TOOL_NAMES}
    sub_specs = {n: TOOL_SPECS[n] for n in allowed}
    sub_system = profile["system_prompt"] + "\n" + render_tool_docs(sub_specs)
    sub_tools_def = build_tools_def(sub_specs)

    # Optional live presentation hooks for the sub-agent's own loop.
    ui = subagent_ui(type_name, task, depth) if callable(subagent_ui) else {}

    # Isolate the parent's "last output" store from the sub-agent's tool calls.
    saved = (_last_tool_output, _last_tool_name, _last_write_diff)
    try:
        answer = run_agent(
            [{"role": "user", "content": task}],
            max_turns=profile["max_turns"], temperature=0.2,
            system_prompt=sub_system, tools_def=sub_tools_def,
            allowed_tools=allowed, depth=depth + 1,
            spin=ui.get("spin"), on_calls=ui.get("on_calls"),
            on_tool=ui.get("on_tool"), on_result=ui.get("on_result"),
            on_escalation=ui.get("on_escalation"),
        )
    except Exception as e:  # a broken sub-run must not kill the parent turn
        answer = f"Sub-agent failed: {e}"
    finally:
        _last_tool_output, _last_tool_name, _last_write_diff = saved

    if ui.get("done"):
        ui["done"](answer)
    return f"[subagent:{type_name}] {answer}"


_PLACEHOLDER_RE = re.compile(r'\{(\w+)\}')


def _safe_format(template: str, args: dict) -> str:
    """Interpolate {name} placeholders WITHOUT str.format's brace semantics.

    Needed for JSON bodies: str.format treats every `{` as a field opener, so
    `{"query": "{query}"}` raises KeyError '"query"'. Here only `{word}` is
    substituted (and only when known), leaving JSON braces untouched. Supports the
    same `{name_q}` url-quoted variants and config defaults as _format_with_args."""
    import urllib.parse

    safe = dict(APP_CONFIG)
    for k, v in (args or {}).items():
        sv = str(v)
        safe[k] = sv
        safe[f"{k}_q"] = urllib.parse.quote(sv)
    return _PLACEHOLDER_RE.sub(
        lambda m: safe[m.group(1)] if m.group(1) in safe else m.group(0),
        template or "")


def _http_placeholder_error(spec: dict, url: str):
    unresolved = _PLACEHOLDER_RE.search(url)
    if not unresolved:
        return None
    key = unresolved.group(1)
    base = key[:-2] if key.endswith("_q") else key
    hint = (f"pass {base}=<value> to the tool" if base in (spec.get("args") or [])
            else f"set {key} in {CONFIG_PATH.name}")
    return (f"'{spec['name']}' has an unresolved placeholder {{{key}}} in its URL - "
            f"{hint}.")


def _exec_http(mode: str, spec: dict, args: dict, timeout: int) -> str:
    """SSRF-guarded HTTP GET/POST with optional auth headers and a jq filter.

    Extra spec fields (all optional):
      headers=H1;;H2   request headers, config placeholders interpolated
      body={...}       POST body (http_post only)
      filter=<jq>      jq expression applied to the response — keeps noisy API
                       JSON (e.g. SearXNG's engines/positions/score metadata) out
      extract=title    return only an HTML page's title
                       of the model's context

    Kept as a tool MODE rather than a shell one-liner so the SSRF guard still
    applies; a `mode=shell` curl would bypass it entirely."""
    import urllib.error
    import urllib.request

    url = _safe_format(spec.get("url") or "{url}", args)
    # Diagnose an unresolved {placeholder} BEFORE the SSRF guard sees it — otherwise a
    # missing config key or a forgotten argument surfaces as the baffling
    # "Blocked: scheme '' is not allowed" instead of naming what's missing.
    placeholder_error = _http_placeholder_error(spec, url)
    if placeholder_error:
        return placeholder_error
    blocked = _ssrf_check(url)
    if blocked:
        return blocked

    headers = {}
    for raw in (spec.get("headers") or "").split(";;"):
        header = _safe_format(raw.strip(), args)
        if not header:
            continue
        # An unresolved {..._api_key} means the credential isn't in config yet —
        # say so instead of sending a bogus header and returning a raw 401.
        missing = _PLACEHOLDER_RE.search(header)
        if missing:
            return (f"'{spec['name']}' is not configured: set {missing.group(1)} in "
                    f"{CONFIG_PATH.name}. Until then use another search tool.")
        if ":" not in header:
            return f"'{spec['name']}' has an invalid HTTP header: {header}"
        key, value = header.split(":", 1)
        headers[key.strip()] = value.strip()
    data = None
    if mode == "http_post":
        body = _safe_format(spec.get("body") or "{}", args)
        data = body.encode()

    class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, request, fp, code, msg, response_headers, new_url):
            redirect_blocked = _ssrf_check(new_url)
            if redirect_blocked:
                raise urllib.error.URLError(redirect_blocked)
            return super().redirect_request(
                request, fp, code, msg, response_headers, new_url)

    request = urllib.request.Request(
        url, data=data, headers=headers, method="POST" if mode == "http_post" else "GET")
    opener = urllib.request.build_opener(SafeRedirectHandler())
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = response.read(MAX_HTTP_BYTES + 1)
            encoding = response.headers.get_content_charset() or "utf-8"
    except urllib.error.HTTPError as exc:
        raw = exc.read(MAX_HTTP_BYTES + 1)
        detail = raw[:MAX_HTTP_BYTES].decode(errors="replace").strip()
        return f"HTTP {exc.code}: {detail or exc.reason}"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return f"HTTP request failed: {exc}"
    if len(raw) > MAX_HTTP_BYTES:
        return f"HTTP response exceeded the {MAX_HTTP_BYTES}-byte limit."
    result = raw.decode(encoding, errors="replace")
    jq_filter = spec.get("filter")
    if jq_filter:
        try:
            filtered = subprocess.run(
                ["jq", "-r", jq_filter], input=result, capture_output=True,
                text=True, timeout=timeout,
            )
            if filtered.returncode == 0:
                result = filtered.stdout
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
    if not result.strip():
        return "HTTP request completed with an empty response."
    if spec.get("extract") == "title":
        match = re.search(r"<title[^>]*>(.*?)</title>", result, re.IGNORECASE | re.DOTALL)
        return re.sub(r"\s+", " ", match.group(1)).strip() if match else "No title"
    return _wrap_untrusted(_strip_special_tokens(result), url)


# ---------------------------------------------------------------------------
# Browser — real page rendering via Playwright (optional dependency)
# ---------------------------------------------------------------------------
BROWSER_TIMEOUT_MS = int(APP_CONFIG.get("browser_timeout_ms", "20000"))


def _playwright_available() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
        return True
    except Exception:
        return False


def _exec_browser(args: dict) -> str:
    """Load a page in a headless browser and return its text, optionally scoped to
    a CSS selector. Handles JS-rendered pages that curl cannot. SSRF-guarded.
    Degrades with install instructions when Playwright isn't present."""
    url = str(args.get("url") or "").strip()
    if not url:
        return "Error: browser tool requires 'url'."
    blocked = _ssrf_check(url)
    if blocked:
        return blocked
    if not _playwright_available():
        return ("Playwright is not installed. Install it with:\n"
                "  pip install playwright && playwright install chromium\n"
                "Until then, use web_search or get_page_title instead.")
    selector = str(args.get("selector") or "").strip()
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                blocked_requests = []

                def guard_request(route):
                    request_url = route.request.url
                    if request_url.startswith(("data:", "blob:", "about:")):
                        route.continue_()
                        return
                    reason = _ssrf_check(request_url)
                    if reason:
                        blocked_requests.append(reason)
                        route.abort()
                    else:
                        route.continue_()

                page.route("**/*", guard_request)
                page.goto(url, timeout=BROWSER_TIMEOUT_MS, wait_until="domcontentloaded")
                if blocked_requests:
                    return blocked_requests[0]
                if selector:
                    text = "\n".join(el.inner_text() for el in page.query_selector_all(selector))
                else:
                    text = page.inner_text("body")
                title = page.title()
            finally:
                browser.close()
    except Exception as e:
        return f"Browser error: {e}"
    text = re.sub(r'\n{3,}', '\n\n', (text or "").strip())
    return _wrap_untrusted(_strip_special_tokens(f"Title: {title}\n\n{text[:5000]}"), url)


# ---------------------------------------------------------------------------
# Sandboxed execution — native OS isolation, with Docker as a fallback
# ---------------------------------------------------------------------------
DOCKER_IMAGE = APP_CONFIG.get("docker_image", "python:3.11-slim")
DOCKER_NETWORK = APP_CONFIG.get("docker_network", "none")
_DOCKER_IMAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:@-]{0,254}$")
_SANDBOX_BACKENDS = frozenset(("auto", "native", "docker", "local"))


def _agent_data_dir() -> Path:
    if os.environ.get("AGENT8088_HOME"):
        return Path(os.environ["AGENT8088_HOME"]).expanduser()
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "agent8088"
    return Path.home() / ".agent8088"


def _native_sandbox_argv():
    override = os.environ.get("AGENT8088_SRT")
    if override:
        return shlex.split(override, posix=sys.platform != "win32")
    cli = (_agent_data_dir() / "runtime" / "node_modules"
           / "@anthropic-ai" / "sandbox-runtime" / "dist" / "cli.js")
    node = shutil.which("node")
    if node and cli.exists():
        return [node, str(cli)]
    executable = shutil.which("srt")
    return [executable] if executable else None


def _native_sandbox_missing_requirements() -> list:
    if not _native_sandbox_argv():
        return ["sandbox-runtime"]
    if sys.platform == "darwin":
        required = ("sandbox-exec", "rg")
    elif sys.platform.startswith("linux"):
        required = ("bwrap", "socat", "rg")
    else:
        required = ()
    return [command for command in required if not shutil.which(command)]


def _docker_available() -> bool:
    docker = shutil.which("docker")
    if not docker:
        return False
    try:
        return subprocess.run(
            [docker, "info"], capture_output=True, timeout=10
        ).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _resolve_sandbox_backend() -> str:
    requested = SANDBOX_BACKEND if SANDBOX_BACKEND in _SANDBOX_BACKENDS else "auto"
    native_available = not _native_sandbox_missing_requirements()
    if requested == "local":
        return "local"
    if requested == "native":
        return "native" if native_available else "unavailable"
    if requested == "docker":
        return "docker" if _docker_available() else "unavailable"
    if native_available:
        return "native"
    return "docker" if _docker_available() else "unavailable"


def sandbox_status() -> dict:
    resolved = _resolve_sandbox_backend()
    detail = {
        "native": "OS-native isolation via sandbox-runtime",
        "docker": "Docker fallback with no network and capped resources",
        "local": "unsandboxed local execution (explicit opt-in)",
        "unavailable": "native runtime and Docker are unavailable",
    }[resolved]
    missing = _native_sandbox_missing_requirements()
    if resolved == "unavailable" and missing:
        detail += f" (missing: {', '.join(missing)})"
    return {
        "requested": SANDBOX_BACKEND,
        "resolved": resolved,
        "detail": detail,
        "network": ", ".join(SANDBOX_ALLOWED_DOMAINS) or "blocked",
        "runtime_version": SANDBOX_RUNTIME_VERSION,
    }


def set_sandbox_backend(backend: str) -> dict:
    global SANDBOX_BACKEND
    backend = str(backend or "").strip().lower()
    if backend not in _SANDBOX_BACKENDS:
        raise ValueError("Sandbox must be auto, native, docker, or local.")
    update_simple_config(CONFIG_PATH, {"sandbox_backend": backend})
    APP_CONFIG["sandbox_backend"] = backend
    SANDBOX_BACKEND = backend
    return sandbox_status()


def _sandbox_settings_data() -> dict:
    home = Path.home()
    denied = [
        CONFIG_PATH, _agent_data_dir() / "srt-settings.json",
        home / ".ssh", home / ".aws", home / ".gnupg", home / ".kube",
        home / ".azure", home / ".config" / "gcloud", home / ".config" / "gh",
        home / ".docker" / "config.json", home / ".npmrc", home / ".netrc",
        PROJECT_ROOT / "**" / ".env*", PROJECT_ROOT / "**" / "*.pem",
        PROJECT_ROOT / "**" / "*.key", PROJECT_ROOT / "**" / "*.p12",
        PROJECT_ROOT / "**" / "*_KEY*",
        PROJECT_ROOT / "**" / "*_SECRET*", PROJECT_ROOT / "**" / "*_TOKEN*",
        PROJECT_ROOT / "**" / "*_PASSWORD*",
    ]
    deny_paths = [str(path.expanduser().resolve()) for path in denied]
    allow_write = [str(PROJECT_ROOT), tempfile.gettempdir()]
    if sys.platform != "win32":
        allow_write.append(str(Path("/tmp").resolve()))
    allow_write.extend(str(path) for path in NO_PROMPT_PATHS)
    return {
        "network": {
            "allowedDomains": SANDBOX_ALLOWED_DOMAINS,
            "deniedDomains": [],
            "allowLocalBinding": False,
        },
        "filesystem": {
            "denyRead": deny_paths,
            "allowRead": [],
            "allowWrite": list(dict.fromkeys(allow_write)),
            "denyWrite": deny_paths + [str(path) for path in BLOCKED_PATHS],
        },
        "enableWeakerNestedSandbox": False,
        "enableWeakerNetworkIsolation": False,
        "allowAppleEvents": False,
    }


def _write_sandbox_settings() -> Path:
    path = _agent_data_dir() / "srt-settings.json"
    _write_private_text(path, json.dumps(_sandbox_settings_data(), indent=2) + "\n")
    return path


def _exec_native_sandbox(command: str, timeout: int) -> str:
    argv = _native_sandbox_argv()
    if not argv:
        return "Native sandbox runtime is unavailable."
    settings = _write_sandbox_settings()
    return _exec_process(
        argv + ["--settings", str(settings), "-c", command], timeout=timeout
    )


def _exec_sandbox_argv(argv: list, timeout: int = 25) -> str:
    global _local_fallback_grant, _one_shot_grant
    backend = _resolve_sandbox_backend()
    if backend == "native":
        runtime = _native_sandbox_argv()
        settings = _write_sandbox_settings()
        return _exec_process(
            runtime + ["--settings", str(settings), *argv], timeout=timeout
        )
    if backend == "docker":
        return _exec_docker_command(_process_display(argv), timeout)
    using_fallback_grant = _local_fallback_grant
    if backend == "local" or using_fallback_grant or PERMISSION_MODE == "edit":
        _local_fallback_grant = False
        if using_fallback_grant:
            _one_shot_grant = False
        return _exec_process(argv, timeout=timeout)
    return _local_execution_request(_process_display(argv))


def _exec_docker_command(command: str, timeout: int, python_code: bool = False,
                         image: str = "") -> str:
    selected_image = image or DOCKER_IMAGE
    if not _DOCKER_IMAGE_RE.fullmatch(selected_image):
        return f"Error: invalid container image name: {selected_image}"
    workspace = str(PROJECT_ROOT)
    container_name = f"agent8088-{os.getpid()}-{uuid.uuid4().hex[:12]}"
    container_command = ["python", "-c", command] if python_code else ["sh", "-lc", command]
    argv = [
        "docker", "run", "--rm", "--name", container_name, "--network", DOCKER_NETWORK,
        "--memory", "512m", "--cpus", "1", "--pids-limit", "256",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--mount", f"type=bind,src={workspace},dst=/workspace",
    ]
    empty = _agent_data_dir() / "sandbox-empty"
    empty.parent.mkdir(parents=True, exist_ok=True)
    empty.touch(mode=0o600, exist_ok=True)
    skipped_dirs = {".git", ".venv", "venv", "node_modules", "__pycache__", "build", "dist"}
    sensitive_mounts = 0
    for root, dirs, files in os.walk(PROJECT_ROOT):
        dirs[:] = [name for name in dirs if name not in skipped_dirs]
        for filename in files:
            path = Path(root) / filename
            if not _is_sensitive_path(str(path)):
                continue
            sensitive_mounts += 1
            if sensitive_mounts > 128:
                return "Error: too many sensitive workspace files to mask safely."
            relative = path.relative_to(PROJECT_ROOT).as_posix()
            destination = f"/workspace/{relative}"
            argv.extend([
                "--mount", f"type=bind,src={empty},dst={destination},readonly",
            ])
    argv.extend(["-w", "/workspace", selected_image, *container_command])
    result = _exec_process(argv, timeout=timeout)
    if "timed out" in result:
        try:
            subprocess.run(
                ["docker", "rm", "-f", container_name],
                capture_output=True, text=True, timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
    return result


def _local_execution_request(command: str) -> str:
    return request_escalation(
        target_mode="edit",
        paths=[str(SHELL_CWD)],
        change_type="local_execution",
        reason=(
            "No native sandbox or Docker fallback is available. "
            f"Run this command locally without isolation? {_redact_secrets(command[:160])}"
        ),
    )


def _exec_sandbox_command(command: str, timeout: int = 25,
                          python_code: bool = False, image: str = "") -> str:
    global _local_fallback_grant, _one_shot_grant
    backend = _resolve_sandbox_backend()
    if backend == "native":
        local_command = (
            _process_display([sys.executable, "-c", command])
            if python_code else command
        )
        return _exec_native_sandbox(local_command, timeout)
    if backend == "docker":
        return _exec_docker_command(command, timeout, python_code, image)
    using_fallback_grant = _local_fallback_grant
    if backend == "local" or using_fallback_grant or PERMISSION_MODE == "edit":
        _local_fallback_grant = False
        if using_fallback_grant:
            _one_shot_grant = False
        if python_code:
            return _exec_process([sys.executable, "-c", command], timeout=timeout)
        return _exec_process(command, timeout=timeout, shell=True)
    return _local_execution_request(command)


def install_native_sandbox() -> str:
    node = shutil.which("node")
    npm = shutil.which("npm")
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
    result = _exec_process([
        npm, "install", "--prefix", str(runtime_dir), "--no-audit", "--no-fund",
        f"@anthropic-ai/sandbox-runtime@{SANDBOX_RUNTIME_VERSION}",
    ], timeout=300)
    if "exited with status" in result or "timed out" in result:
        return result
    if sys.platform == "win32":
        runtime = _native_sandbox_argv()
        if not runtime:
            return "Native sandbox runtime installed but its CLI could not be located."
        setup = _exec_process([*runtime, "windows-install"], timeout=300)
        if "exited with status" in setup or "timed out" in setup:
            return setup
    missing = _native_sandbox_missing_requirements()
    if missing:
        return (
            f"Native sandbox runtime {SANDBOX_RUNTIME_VERSION} installed. "
            f"Install the remaining OS packages: {', '.join(missing)}."
        )
    return f"Native sandbox runtime {SANDBOX_RUNTIME_VERSION} installed."


def _tool_arg_parse_error(name: str, raw: str) -> str:
    """Message for an argument block that arrived but could not be parsed.

    Distinct from "the argument is missing" on purpose: telling the model an
    argument is absent when it did send one sends it chasing the wrong problem.
    """
    return (f"Error: could not parse the arguments for '{name}'. Send valid "
            f"JSON with newlines escaped as \\n, e.g. "
            f'{{"code": "a = 1\\nprint(a)"}}. Received: {raw[:200]}')


_CODE_ARG_ALIASES = ("code", "script", "python", "source", "snippet", "command")
_FENCE_RE = re.compile(r"^\s*```[a-zA-Z0-9_+-]*\s*\n(.*?)\n?\s*```\s*$", re.DOTALL)


def _strip_code_fences(code: str) -> str:
    """Drop a surrounding ```lang ... ``` fence, which models often add."""
    match = _FENCE_RE.match(code)
    return match.group(1) if match else code


def _exec_docker(args: dict) -> str:
    """Run a Python snippet through the configured sandbox backend."""
    if args.get("__parse_error__"):
        return _tool_arg_parse_error("run_sandboxed", args["__parse_error__"])
    # Accept the obvious synonyms — the model frequently names this argument
    # 'script' or 'python'. 'code' wins when more than one is present.
    code = ""
    for alias in _CODE_ARG_ALIASES:
        value = str(args.get(alias) or "").strip()
        if value:
            code = value
            break
    code = _strip_code_fences(code).strip()
    if not code:
        return ("Error: sandboxed execution requires 'code'. Pass the Python "
                "source as code=\"...\" (newlines escaped as \\n).")
    image = str(args.get("image") or DOCKER_IMAGE)
    timeout = int(args.get("timeout") or 60)
    return _exec_sandbox_command(code, timeout=timeout, python_code=True, image=image)


_CRON_FIELD_RE = re.compile(r'^[\d\*/,\-]+$')
_CRON_MARKER = "# agent8088"
_WINDOWS_TASK_PREFIX = "Agent8088-"


def _windows_schedule_args(fields: list) -> list:
    minute, hour, day, month, weekday = fields
    if month != "*":
        raise ValueError("month-specific schedules")

    def number(value, low, high, label):
        if not value.isdigit() or not low <= int(value) <= high:
            raise ValueError(label)
        return int(value)

    if day == "*" and weekday == "*" and hour == "*":
        if minute == "*":
            return ["/SC", "MINUTE", "/MO", "1"]
        if minute.startswith("*/"):
            interval = number(minute[2:], 1, 59, "minute interval")
            return ["/SC", "MINUTE", "/MO", str(interval), "/ST", "00:00"]
        minute_value = number(minute, 0, 59, "minute")
        return ["/SC", "HOURLY", "/MO", "1", "/ST", f"00:{minute_value:02d}"]

    minute_value = number(minute, 0, 59, "minute")
    hour_value = number(hour, 0, 23, "hour")
    start = f"{hour_value:02d}:{minute_value:02d}"
    if day == "*" and weekday == "*":
        return ["/SC", "DAILY", "/ST", start]
    if day != "*" and weekday == "*":
        day_value = number(day, 1, 31, "day of month")
        return ["/SC", "MONTHLY", "/D", str(day_value), "/ST", start]
    if day == "*" and weekday != "*":
        names = ("SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
        values = weekday.split(",")
        if not values or any(not value.isdigit() or not 0 <= int(value) <= 7
                             for value in values):
            raise ValueError("weekday")
        days = ",".join(dict.fromkeys(names[int(value)] for value in values))
        return ["/SC", "WEEKLY", "/D", days, "/ST", start]
    raise ValueError("combined day-of-month and weekday schedules")


def _windows_schedule_registry_path() -> Path:
    return _agent_data_dir() / "scheduled-tasks.json"


def _load_windows_schedules() -> list:
    path = _windows_schedule_registry_path()
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return entries if isinstance(entries, list) else []


def _save_windows_schedules(entries: list) -> None:
    _write_private_text(
        _windows_schedule_registry_path(),
        json.dumps(entries, indent=2) + "\n",
    )


def _windows_task_script(identifier: str, task: str) -> Path:
    import base64

    scripts = _agent_data_dir() / "scheduled-tasks"
    script = scripts / f"{identifier}.ps1"
    prompt = base64.b64encode(task.encode("utf-8")).decode("ascii")
    cwd = str(SHELL_CWD).replace("'", "''")
    agent = str(shutil.which("agent8088") or "agent8088").replace("'", "''")
    content = (
        "$ErrorActionPreference = 'Stop'\n"
        f"Set-Location -LiteralPath '{cwd}'\n"
        f"$prompt = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{prompt}'))\n"
        f"$prompt | & '{agent}'\n"
    )
    _write_private_text(script, content)
    return script


def _exec_windows_cron(action: str, schedule: str = "", task: str = "",
                       fields: list = None) -> str:
    import hashlib

    entries = _load_windows_schedules()
    if action == "list":
        lines = [
            f"{entry.get('schedule', '')} {entry.get('task', '')} {_CRON_MARKER}"
            for entry in entries
            if re.fullmatch(r"[0-9a-f]{16}", str(entry.get("id", "")))
        ]
        return "\n".join(lines) or "No scheduled tasks."

    scheduler = shutil.which("schtasks.exe") or shutil.which("schtasks") or "schtasks.exe"
    if action == "add":
        try:
            schedule_args = _windows_schedule_args(fields or [])
        except ValueError as exc:
            return f"Unsupported Windows schedule ({exc})."
        identifier = hashlib.sha256(
            f"{schedule}\0{task}\0{SHELL_CWD}".encode("utf-8")
        ).hexdigest()[:16]
        task_name = f"{_WINDOWS_TASK_PREFIX}{identifier}"
        try:
            script = _windows_task_script(identifier, task)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"Windows scheduler error: {exc}"
        powershell = shutil.which("powershell.exe") or shutil.which("pwsh.exe") or "powershell.exe"
        task_command = (
            f'"{powershell}" -NoProfile -NonInteractive '
            f'-ExecutionPolicy Bypass -File "{script}"'
        )
        try:
            result = subprocess.run(
                [scheduler, "/Create", "/TN", task_name, "/TR", task_command,
                 *schedule_args, "/RL", "LIMITED", "/IT", "/F"],
                capture_output=True, text=True, timeout=20,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            script.unlink(missing_ok=True)
            return f"Windows scheduler error: {exc}"
        if result.returncode:
            script.unlink(missing_ok=True)
            return f"Windows scheduler error: {result.stderr.strip() or result.stdout.strip()}"
        updated = [entry for entry in entries if entry.get("id") != identifier]
        updated.append({"id": identifier, "schedule": schedule, "task": task})
        try:
            _save_windows_schedules(updated)
        except (OSError, subprocess.TimeoutExpired) as exc:
            try:
                subprocess.run(
                    [scheduler, "/Delete", "/TN", task_name, "/F"],
                    capture_output=True, text=True, timeout=20,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
            script.unlink(missing_ok=True)
            return f"Windows scheduler error: {exc}"
        return f"Scheduled: {schedule}"

    matches = [
        entry for entry in entries
        if entry.get("task") == task
        and re.fullmatch(r"[0-9a-f]{16}", str(entry.get("id", "")))
    ]
    if not matches:
        return "No matching scheduled task."
    failures = []
    removed_ids = set()
    for entry in matches:
        identifier = entry["id"]
        try:
            result = subprocess.run(
                [scheduler, "/Delete", "/TN",
                 f"{_WINDOWS_TASK_PREFIX}{identifier}", "/F"],
                capture_output=True, text=True, timeout=20,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            failures.append(str(exc))
            continue
        if result.returncode:
            failures.append(result.stderr.strip() or result.stdout.strip())
            continue
        removed_ids.add(identifier)
        (_agent_data_dir() / "scheduled-tasks" / f"{identifier}.ps1").unlink(
            missing_ok=True)
    if removed_ids:
        try:
            _save_windows_schedules(
                [entry for entry in entries if entry.get("id") not in removed_ids])
        except (OSError, subprocess.TimeoutExpired) as exc:
            failures.append(f"could not update schedule registry: {exc}")
    if failures:
        return f"Windows scheduler error: {'; '.join(filter(None, failures))}"
    return "Removed."


def _exec_cron(args: dict) -> str:
    """Manage scheduled runs of this agent via the user's crontab.
    actions: list | add (schedule, task) | remove (task)."""
    action = str(args.get("action") or "list").strip().lower()
    if action not in ("list", "add", "remove"):
        return f"Unknown cron action '{action}'. Use list, add, or remove."
    schedule = ""
    task = ""
    fields = []
    if action == "add":
        schedule = str(args.get("schedule") or "").strip()
        task = str(args.get("task") or "").strip()
        fields = schedule.split()
        if len(fields) != 5 or not all(_CRON_FIELD_RE.match(field) for field in fields):
            return ("Invalid schedule. Use 5 cron fields, e.g. '0 9 * * *' "
                    "(minute hour day month weekday).")
        if not task:
            return "Error: cron 'add' requires a task."
        if any(char in task for char in ("\0", "\r", "\n")):
            return "Error: cron task must be a single line."
    elif action == "remove":
        task = str(args.get("task") or "").strip()
        if not task:
            return "Error: cron 'remove' requires the task text to match."
        if any(char in task for char in ("\0", "\r", "\n")):
            return "Error: cron task must be a single line."

    if sys.platform == "win32":
        return _exec_windows_cron(action, schedule, task, fields)

    def read_crontab():
        result = subprocess.run(
            ["crontab", "-l"], capture_output=True, text=True, timeout=20)
        return "" if result.returncode else result.stdout

    if action == "list":
        entries = [line for line in read_crontab().splitlines() if _CRON_MARKER in line]
        return "\n".join(entries) or "No scheduled tasks."

    if action == "add":
        agent = shutil.which("agent8088") or "agent8088"
        entry = (f"{schedule} cd {shlex.quote(str(SHELL_CWD))} && "
                 f"printf '%s\\n' {shlex.quote(task)} | {shlex.quote(agent)} {_CRON_MARKER}")
        current = read_crontab()
        payload = current + ("" if not current or current.endswith("\n") else "\n") + entry + "\n"
        result = subprocess.run(
            ["crontab", "-"], input=payload, capture_output=True, text=True, timeout=20)
        return f"Scheduled: {schedule}" if result.returncode == 0 else f"Cron error: {result.stderr.strip()}"

    if action == "remove":
        quoted_task = shlex.quote(task)
        payload = "\n".join(
            line for line in read_crontab().splitlines()
            if not (_CRON_MARKER in line and quoted_task in line)
        )
        if payload:
            payload += "\n"
        result = subprocess.run(
            ["crontab", "-"], input=payload, capture_output=True, text=True, timeout=20)
        return "Removed." if result.returncode == 0 else f"Cron error: {result.stderr.strip()}"

    raise AssertionError(f"Unhandled cron action: {action}")


def _tool_path(spec: dict, args: dict) -> str:
    path_arg = spec.get("path_arg") or "filename"
    return (args.get(path_arg) or args.get("filename") or args.get("file")
            or args.get("file_path") or args.get("filepath") or args.get("path") or "")


def run_tool(name: str, args: dict, allow_plan: bool = True, depth: int = 0) -> str:
    global _remote_git_grant
    spec = TOOL_SPECS.get(name)
    if not spec:
        return f"Unknown tool: {name}"

    mode = (spec.get("mode") or "").lower()
    timeout = int(spec.get("timeout") or 25)

    # --- Plan-only early gate: block gated tools BEFORE arg validation ---
    # Without this, write_file() with no args returns "write tool requires a file path"
    # instead of telling the model to use execute_plan — the model never learns why.
    # allow_plan=False means we're INSIDE _exec_plan (a plan step) — let it through
    # to the normal check_permission gate so it escalates properly.
    if (PERMISSION_MODE == "plan-only"
            and allow_plan
            and mode in ("write_text", "shell", "docker", "cron", "browser")):
        return ("Error: plan-only mode — direct tool execution blocked. "
                "Call the execute_plan tool with a JSON steps array, e.g.: "
                'execute_plan(steps=[{"tool":"write_file",'
                '"arguments":{"filename":"C:/tmp/x.txt","content":"hi"}}]) '
                "Do NOT describe the plan in prose — call execute_plan as a tool call.")

    # --- Layer 1: Sensitive file read protection (before anything else) ---
    read_target = None
    if mode == "read_text":
        try:
            read_target = resolve_user_path(_tool_path(spec, args))
        except ValueError as exc:
            return f"Error: {exc}"
        if _is_sensitive_path(str(read_target)):
            return f"Error: Access to sensitive file denied: {read_target}"

    # --- Layer 2: Network access control ---
    if mode in ("http_get", "http_post"):
        url = _safe_format(spec.get("url") or "{url}", args)
        placeholder_error = _http_placeholder_error(spec, url)
        if placeholder_error:
            return placeholder_error
        blocked = _ssrf_check(url)
        if blocked:
            return blocked
        if not check_permission(mode, url):
            return request_escalation(
                target_mode="edit",
                paths=[url[:120]],
                change_type="network_request",
                reason=f"Tool '{name}' wants to make an HTTP request to: {url[:200]}",
            )
        return _exec_http(mode, spec, args, timeout)

    # --- Permission gate for writes, shell, containers, cron, and browser ---
    command = ""
    write_path = ""
    path_zone = "default"
    if mode == "shell":
        try:
            argv = _structured_tool_argv(name, args)
        except ValueError as exc:
            return f"Error: {exc}"
        command = _process_display(argv) if argv else _format_with_args(
            spec.get("command") or "{command}", args)
    elif mode == "write_text":
        command = "write_file"
        write_path = _tool_path(spec, args)
        if not write_path:
            return "Error: write tool requires a file path."
        try:
            target = resolve_user_path(write_path)
        except ValueError as exc:
            return f"Error: {exc}"
        # Layer 1 applies to WRITES as well as reads. Without this a sensitive file
        # (~/.gitconfig, ~/.ssh/authorized_keys, .env, a key file) could be silently
        # overwritten even though reading it is denied.
        if _is_sensitive_path(str(target)):
            return f"Error: Writing to sensitive file denied: {target}"
        # Writing a shell startup file is code execution on the next shell
        # launch. Refused unconditionally — no mode and no grant unlocks it.
        if _is_shell_startup_file(str(target)):
            return (f"Error: Writing to sensitive file denied: {target} "
                    f"(shell startup file — this would execute code on the next shell launch)")
        path_zone = _check_path_zone(target)
        if path_zone == "blocked":
            return f"Error: Write path is blocked: {target}"
    elif mode == "cron":
        command = str(args.get("action") or "list").strip().lower()
    elif mode == "browser":
        command = str(args.get("url") or "").strip()
        if not command:
            return "Error: browser tool requires 'url'."
        blocked = _ssrf_check(command)
        if blocked:
            return blocked
    elif mode == "mcp":
        command = f"{spec['mcp_server']}:{spec['mcp_tool']}"

    remote_git_approved = name == "git_push" and _remote_git_grant
    if name == "git_push" and not remote_git_approved:
        return request_escalation(
            target_mode="edit",
            paths=["origin HEAD"],
            change_type="git_remote_write",
            reason="Push the current branch to origin? This changes a remote repository.",
        )
    if remote_git_approved:
        _remote_git_grant = False

    if mode == "shell" and _hard_blocked_shell(command) and not remote_git_approved:
        return "Error: This git operation is forbidden by Agent8088's safety policy."

    gated_modes = ("write_text", "shell", "docker", "cron", "browser", "mcp")
    if mode == "mcp" and spec.get("mcp_read_only"):
        gated_modes = tuple(item for item in gated_modes if item != "mcp")
    if mode in gated_modes and not remote_git_approved and not check_permission(
            mode, command, path_zone, bool(spec.get("host"))):
        if PERMISSION_MODE == "plan-only" and allow_plan:
            return ("Error: plan-only mode — direct tool execution blocked. "
                    "Call the execute_plan tool with a JSON steps array, e.g.: "
                    'execute_plan(steps=[{"tool":"write_file",'
                    '"arguments":{"filename":"/tmp/x","content":"hi"}}]) '
                    "Do NOT describe the plan in prose — call execute_plan as a tool call.")
        paths_str = ""
        if mode == "write_text":
            paths_str = str(target)
        elif mode == "shell":
            paths_str = command[:80]
        elif mode == "docker":
            paths_str = "sandboxed_code"
        elif mode == "cron":
            paths_str = command
        elif mode == "browser":
            paths_str = command[:120]
        elif mode == "mcp":
            paths_str = command
        sandbox_missing = mode in ("shell", "docker") and _resolve_sandbox_backend() == "unavailable"
        change_type = {
            "write_text": "new_file",
            "cron": "scheduled_task",
            "browser": "network_request",
            "mcp": "mcp_tool",
        }.get(mode, "local_execution" if sandbox_missing else "filesystem_op")
        reason = (
            f"Tool '{name}' needs permission and no sandbox is available. "
            "Run this action locally without isolation?"
            if sandbox_missing else
            f"Tool '{name}' requires {mode} access, which is blocked in readonly mode."
        )
        return request_escalation(
            target_mode="edit",
            paths=[paths_str],
            change_type=change_type,
            reason=reason,
        )

    if mode == "last_output":
        if not _last_tool_output:
            return "No tool has been run yet."
        return f"Full output from '{_last_tool_name}' ({len(_last_tool_output)} chars):\n\n{_last_tool_output}"

    if mode == "plan":
        if not allow_plan:
            return "Error: Nested plan tool execution is not allowed."
        return _exec_plan(args, on_step=_plan_on_step,
                          on_escalation=_plan_on_escalation, depth=depth)

    if mode == "subagent":
        return _exec_subagent(args, depth=depth)

    if mode == "cron":
        return _exec_cron(args)

    if mode == "docker":
        return _exec_docker(args)

    if mode == "browser":
        return _exec_browser(args)

    if mode == "mcp":
        return _wrap_untrusted(MCP_RUNTIME.call(name, args), f"MCP {command}")

    if mode == "read_text":
        return _strip_special_tokens(_read_text_limited(read_target))

    if mode == "write_text":
        global _last_write_diff
        content_arg = spec.get("content_arg") or "content"
        content = str(args.get(content_arg, ""))
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            old_content = _read_text_limited(target) if target.exists() else ""
        except ValueError:
            old_content = ""
        if args.get("_private") is True:
            _write_private_text(target, content)
        else:
            target.write_text(content)
        _last_write_diff = _make_diff(old_content, content, str(target))
        return f"Wrote {len(content)} bytes to {target}"

    if mode == "python_eval":
        expression = spec.get("expression") or args.get("expression") or ""
        if expression:
            expression = _format_with_args(expression, args)
        try:
            return str(_safe_calculate(expression))
        except (SyntaxError, TypeError, ValueError, ZeroDivisionError, OverflowError) as exc:
            return f"Error: invalid arithmetic expression: {exc}"

    if mode == "shell":
        if _structured_tool_argv(name, args):
            return _exec_structured_tool(name, args, timeout)
        command = _format_with_args(spec.get("command") or "{command}", args)
        if spec.get("host"):
            return _missing_binary_hint(
                command.split()[0] if command.split() else "",
                _exec_process(command, timeout=timeout, shell=True))
        return _exec_shell_command(command, timeout=timeout)

    return f"Unknown tool mode '{mode}' for tool '{name}'"


def _make_diff(old: str, new: str, filename: str) -> list:
    """Return a unified diff as a list of lines for Rich UI colorized display."""
    import difflib
    if old == new:
        return []
    return list(difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile=f"{filename} (old)", tofile=filename, lineterm="",
    ))


def exec_tool(name: str, arguments: str, depth: int = 0) -> str:
    global _last_tool_output, _last_tool_name
    try:
        args = json.loads(arguments)
    except Exception:
        return "Invalid JSON"

    try:
        result = run_tool(name, args, depth=depth)
    except subprocess.TimeoutExpired:
        result = "Command timed out"
    except Exception as e:
        result = f"Error: {e}"

    # Redact config secrets (api keys/tokens) so tool output can't exfiltrate them.
    result = _redact_secrets(result)

    if (TOOL_SPECS.get(name, {}).get("mode") or "").lower() != "last_output":
        _last_tool_output, _last_tool_name = result, name
    return result


# ---------------------------------------------------------------------------
# Parsing model output for tool calls
# ---------------------------------------------------------------------------
_JSON_CONTROL_ESCAPES = {"\n": "\\n", "\r": "\\r", "\t": "\\t"}


def _escape_control_chars_in_strings(raw: str) -> str:
    """Escape literal newlines/tabs that appear inside JSON string values.

    Models routinely emit real newlines inside an argument value when the value
    is code or file content:

        ✿ARGS✿: {"code": "a = 1
        print(a)"}

    That is invalid JSON, so json.loads raises. Rather than lose the call, walk
    the text and escape control characters found inside string literals. Tracks
    escape state so an already-escaped `\\n` is left alone and a literal
    backslash is not mistaken for an escape of the following quote.
    """
    out = []
    in_string = False
    escaped = False
    for char in raw:
        if escaped:
            out.append(char)
            escaped = False
            continue
        if char == "\\":
            out.append(char)
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
            out.append(char)
            continue
        out.append(_JSON_CONTROL_ESCAPES.get(char, char) if in_string else char)
    return "".join(out)


def _loads_tool_args(raw: str):
    """json.loads for model-emitted arguments, tolerant of unescaped newlines.

    Raises the original JSONDecodeError if the text is broken beyond escaping,
    so callers can distinguish "unparseable" from "no arguments given".
    """
    try:
        return json.loads(raw)
    except ValueError:
        return json.loads(_escape_control_chars_in_strings(raw))


def find_tool_calls(text: str, allowed: set = None) -> list:
    allowed = allowed if allowed is not None else TOOL_NAMES
    calls = []
    # 1) ✿{"name": "...", "arguments": {...}}✿
    for m in re.finditer(r'✿(.*?)✿', text, re.DOTALL):
        try:
            d = _loads_tool_args(m.group(1).strip())
            resolved = _resolve_tool_name(d.get("name", ""))
            if resolved in allowed:
                d["name"] = resolved
                d["arguments"] = d.get("arguments", {})
                calls.append(d)
        except Exception:
            pass
    # 2) ✿FUNCTION✿: name ✿ARGS✿: {...}
    # Greedy match (\{.*\}) captures nested JSON braces — non-greedy (\{.*?\})
    # stops at the first }, truncating args like {"steps": "[{\"tool\": ...}]"}
    # and causing the args to fail parsing, falling through to empty-args match.
    if not calls:
        m = re.search(r'✿FUNCTION✿\s*:\s*(\w+)\s*✿ARGS✿\s*:\s*(\{.*\})', text, re.DOTALL)
        if m:
            resolved = _resolve_tool_name(m.group(1))
            if resolved in allowed:
                try:
                    calls.append({"name": resolved, "arguments": _loads_tool_args(m.group(2))})
                except Exception:
                    # An ARGS block was sent but is unparseable. Surfacing empty
                    # args here would make the tool report the argument as
                    # missing, which sends the model chasing the wrong problem.
                    # Flag the parse failure instead.
                    calls.append({"name": resolved,
                                  "arguments": {"__parse_error__": m.group(2)[:400]}})
        if not calls and "✿ARGS✿" not in text:  # loose ✿FUNCTION✿ line, genuinely no args
            m2 = re.search(r'✿FUNCTION✿\s*:\s*(\w+)', text)
            if m2:
                resolved = _resolve_tool_name(m2.group(1))
                if resolved in allowed:
                    calls.append({"name": resolved, "arguments": {}})
    # 3) bare JSON {"name": "...", "arguments": {...}}
    if not calls:
        for m in re.finditer(r'\{\s*"name"\s*:\s*"(\w+)"\s*,\s*"arguments"\s*:\s*(\{.*?\})\s*\}', text, re.DOTALL):
            try:
                resolved = _resolve_tool_name(m.group(1))
                if resolved in allowed:
                    calls.append({"name": resolved, "arguments": json.loads(m.group(2))})
                    break
            except Exception:
                pass
    # 4) tool name followed by an inline {"command": "..."}
    if not calls:
        for name in allowed:
            m = re.search(re.escape(name) + r'\s*\{\s*"command"\s*:\s*"([^"]+)"', text)
            if m:
                calls.append({"name": name, "arguments": {"command": m.group(1).replace('\\"', '"')}})
                break
        if not calls:
            for alias, canonical in TOOL_ALIASES.items():
                m = re.search(re.escape(alias) + r'\s*\{\s*"command"\s*:\s*"([^"]+)"', text)
                if m and canonical in allowed:
                    calls.append({"name": canonical, "arguments": {"command": m.group(1).replace('\\"', '"')}})
                    break
    # 5) <|mask_start|>{"tool": "...", "arguments": {...}}<|mask_end|>
    if not calls:
        m = re.search(r'<\|mask_start\|>\s*(\{.*?\})\s*<\|mask_end\|>', text, re.DOTALL)
        if m:
            try:
                d = json.loads(m.group(1).strip())
                tool_name = d.get("tool", d.get("name", ""))
                resolved = _resolve_tool_name(tool_name)
                if resolved in allowed:
                    calls.append({"name": resolved, "arguments": d.get("arguments", {})})
            except Exception:
                pass
    return calls


def strip_tool_json(text: str) -> str:
    text = re.sub(r'<tool_call>.*?</tool_call>', '', text, flags=re.DOTALL)
    text = re.sub(r'<\|mask_start\|>.*?<\|mask_end\|>', '', text, flags=re.DOTALL)
    text = re.sub(r'✿FUNCTION✿.*?✿ARGS✿\s*:\s*\{.*?\}', '', text, flags=re.DOTALL)
    text = re.sub(r'✿FUNCTION✿[^\n]*', '', text)
    text = re.sub(r'\{\s*"name"\s*:\s*"[^"]+"\s*,\s*"arguments"\s*:\s*\{[^}]*\}\s*\}', '', text, flags=re.DOTALL)
    # Hard sanitize: strip any leftover ✿…✿ fragments and stray sentinels so raw
    # tool-call markup can NEVER leak into a user-facing answer.
    text = re.sub(r'✿[^✿\n]*✿', '', text)
    text = text.replace('✿', '')
    # Tidy whitespace WITHOUT flattening newlines, so multi-line answers survive.
    text = re.sub(r'[ \t]+\n', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _attempted_tool_names(text: str) -> list:
    """Tool names the model *tried* to call, valid or not — used for error handling
    when find_tool_calls() finds nothing runnable (e.g. a hallucinated tool)."""
    names = []
    for m in re.finditer(r'✿FUNCTION✿\s*:\s*(\w+)', text):
        names.append(m.group(1))
    for m in re.finditer(r'"name"\s*:\s*"(\w+)"\s*,\s*"arguments"', text):
        names.append(m.group(1))
    # de-dupe, preserve order
    seen, out = set(), []
    for n in names:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


# ---------------------------------------------------------------------------
# Reasoning handling + safety guardrails
# ---------------------------------------------------------------------------
_THINK_BLOCK_RE = re.compile(
    r'<(think|thinking|reason|reasoning|thought|scratchpad)>.*?</\1>',
    re.DOTALL | re.IGNORECASE)
_THINK_OPEN_RE = re.compile(
    r'<(?:think|thinking|reason|reasoning|thought|scratchpad)>.*$',
    re.DOTALL | re.IGNORECASE)


def _strip_reasoning(text: str) -> str:
    """Remove chain-of-thought blocks so they are never (a) stored in context —
    where they pile up until the request blows the context window and the turn
    crashes — nor (b) shown to the user as the final answer. Handles both a closed
    <think>…</think> and a runaway, never-closed <think>… (drops the tail)."""
    if not text:
        return text
    text = _THINK_BLOCK_RE.sub('', text)
    text = _THINK_OPEN_RE.sub('', text)
    return text.strip()


def collect_secret_values(config: dict, env_values: dict = None) -> list:
    """Secret values from config (api keys / tokens, including per-provider ones)
    — redacted from any tool output or answer so `cat config.txt` / `env` etc.
    can't be used to exfiltrate them. Longest first, so overlapping values mask
    completely rather than leaving a suffix behind.

    Any key ending in `_env` holds the NAME of an environment variable, not the
    secret itself — resolve it before redacting. Check the .env key store first
    (that is where _migrate_keys_to_env puts migrated secrets, and nothing
    exports it into os.environ), then the process environment. This covers both
    `provider.<name>.api_key_env` and the `*_bot_token_env` / `*_app_token_env`
    pointers migration writes for gateway tokens."""
    if env_values is None:
        env_values = load_env_file(ENV_FILE_PATH) if "ENV_FILE_PATH" in globals() else {}
    values = set()
    for key, value in config.items():
        if not isinstance(value, str):
            continue
        if key.lower().endswith("_env"):
            candidate = env_values.get(value) or os.environ.get(value, "")
        else:
            candidate = value
        if (any(part in key.lower() for part in ("key", "token", "secret", "password"))
                and len(candidate) >= 4
                and candidate.lower() not in (
                    "none", "ollama", "sk-dummy", "changeme", "your-api-key",
                )):
            values.add(candidate)
    return sorted(values, key=len, reverse=True)


_SECRET_VALUES = collect_secret_values(APP_CONFIG)


# ponytail: Special tokens that self-hosted chat templates tokenize as structural
# role boundaries. If unstripped, a fetched page containing <|im_start|>system
# could forge a system message. Covers Qwen/ChatML, Llama, Gemma, Mistral, Phi, GPT-OSS.
_SPECIAL_TOKEN_RE = re.compile(
    r"<\|im_start\|>|<\|im_end\|>|<\|start_header_id\|>|<\|end_header_id\|>"
    r"|<\|eot_id\|>|<\|eom_id\|>|\[\/INST\]|\[\/SYS\]"
    r"|<\|begin_of_text\|>|<\|end_of_text\|>|<start_of_turn\|>|<end_of_turn\|>"
)


def _strip_special_tokens(text: str) -> str:
    if not text:
        return text
    return _SPECIAL_TOKEN_RE.sub("", text)


def _redact_secrets(text: str) -> str:
    if not text:
        return text
    for v in sorted(set(_SECRET_VALUES) | set(collect_secret_values(APP_CONFIG)),
                    key=len, reverse=True):
        if v in text:
            text = text.replace(v, "[redacted]")
    return text


_MCP_SPECIAL_TOKENS = re.compile(r"<\|[^>]+\|>|\[/(?:INST|SYS)\]")


def _wrap_untrusted(text: str, source: str = "") -> str:
    """Wrap external content (web pages, MCP tool responses) in boundary markers
    so the model sees it as untrusted data, never instructions."""
    if not text or not text.strip():
        return text
    text = _MCP_SPECIAL_TOKENS.sub("", text)
    tag = f'<<<EXTERNAL_UNTRUSTED_CONTENT source="{source}">>>' if source else "<<<EXTERNAL_UNTRUSTED_CONTENT>>>"
    return f"{tag}\n{text}\n<<<END_UNTRUSTED_CONTENT>>>"


# Distinctive lines of the base system prompt, used to detect a verbatim leak.
_SYSTEM_FINGERPRINTS = [ln.strip() for ln in BASE_SYSTEM_PROMPT.splitlines()
                        if len(ln.strip()) >= 40]


def _is_system_leak(answer: str) -> bool:
    """True if the answer appears to reproduce the confidential system prompt."""
    if not answer or len(answer) < 60:
        return False
    hits = sum(1 for fp in _SYSTEM_FINGERPRINTS if fp in answer)
    if hits >= 2:
        return True
    return len(answer) >= 200 and answer.strip()[:200] in BASE_SYSTEM_PROMPT


def _guard_answer(answer: str) -> str:
    """Final safety net on every answer: block system-prompt leaks and redact
    secrets, no matter what the model produced (defense in depth vs. prompt
    injection / data exfiltration — as in Hermes/Claude/Codex harnesses)."""
    if _is_system_leak(answer):
        return ("I can't share my internal system instructions or configuration. "
                "Tell me what you'd like help with instead.")
    return _redact_secrets(answer)


# Requests that target the agent's own internals — refused instantly (no model
# round-trip) rather than looping for 3k tokens before arriving at the same refusal.
_PROTECTED_TARGET_RE = re.compile(
    r'\b(system\.md|config\.txt|system\s*(prompt|instructions|message)|'
    r'your\s+(system\s*)?(prompt|instructions|rules|config|configuration|guidelines)|'
    r'initial\s+prompt|developer\s+(prompt|message)|the\s+prompt\s+you\s+were\s+given)\b',
    re.IGNORECASE)


def _preflight_refusal(messages) -> str:
    """If the latest user turn asks to reveal internal instructions/config, return a
    ready refusal so run_agent can short-circuit before spending any model tokens.
    Returns None for everything else (the vast majority of prompts)."""
    user_msg = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            user_msg = m.get("content") or ""
            break
    if user_msg and _PROTECTED_TARGET_RE.search(user_msg):
        return ("I can't share my internal instructions, system prompt, or configuration "
                "(including files like system.md or config.txt). Let me know what you'd "
                "like help with instead.")
    return None


# ---------------------------------------------------------------------------
# SSRF protection — block requests to internal/private network ranges
# ---------------------------------------------------------------------------
_ALLOWED_URL_SCHEMES = {"http", "https"}
SSRF_ALLOW_PRIVATE = APP_CONFIG.get("ssrf_allow_private", "0") == "1"
# Specific internal hosts the agent MAY reach (e.g. a self-hosted SearXNG), as
# host or host:port. Far tighter than ssrf_allow_private=1, which opens the whole
# private network — prefer this allowlist.
SSRF_ALLOW_HOSTS = {h.strip().lower()
                    for h in APP_CONFIG.get("ssrf_allow_hosts", "").split(",")
                    if h.strip()}


def _ssrf_check(url: str):
    """Return None if the URL is safe to fetch, else an error string.

    Blocks non-http(s) schemes and any host resolving to a private, loopback,
    link-local (incl. the 169.254.169.254 cloud-metadata endpoint), or reserved
    address — so the agent can't be steered into scanning or attacking the
    internal network.

    Escape hatches, in order of preference:
      ssrf_allow_hosts=host[:port],...  allow only these internal hosts
      ssrf_allow_private=1              allow ALL private ranges (blunt)"""
    import ipaddress
    import socket
    import urllib.parse

    if SSRF_ALLOW_PRIVATE:
        return None
    try:
        parts = urllib.parse.urlparse((url or "").strip())
    except Exception:
        return "Blocked: malformed URL."
    if parts.scheme.lower() not in _ALLOWED_URL_SCHEMES:
        return f"Blocked: scheme '{parts.scheme}' is not allowed (only http/https)."
    try:
        host = parts.hostname
    except Exception:
        return "Blocked: malformed URL host."
    if not host:
        return "Blocked: URL has no host."
    # Explicitly allowlisted internal host (match on host and on host:port).
    if SSRF_ALLOW_HOSTS:
        hl = host.lower()
        if hl in SSRF_ALLOW_HOSTS or (
                parts.port and f"{hl}:{parts.port}" in SSRF_ALLOW_HOSTS):
            return None
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return f"Blocked: could not resolve host '{host}'."
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except Exception:
            return "Blocked: unresolvable address."
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return (f"Blocked: '{host}' resolves to internal address {ip}. "
                    "Requests to private/loopback/link-local networks are not allowed.")
    return None


def _mask_system_content(text: str) -> str:
    """Sanitize text that will be SHOWN to the user (e.g. a reasoning preview):
    redact secrets and blank out any verbatim system-prompt lines. Chain-of-thought
    often quotes the system prompt, so this prevents a leak even in debug views."""
    if not text:
        return text
    text = _redact_secrets(text)
    for fp in _SYSTEM_FINGERPRINTS:
        if fp in text:
            text = text.replace(fp, "[internal instructions hidden]")
    return text


# ---------------------------------------------------------------------------
# Shared agent loop (used by both interactive and one-shot modes)
# ---------------------------------------------------------------------------
def run_agent(messages, *, max_turns=10, temperature=0.1, spin=None,
              on_calls=None, on_tool=None, on_result=None, on_answer=None,
              on_escalation=None,
              on_token=None, interrupt_check=None, trace=None,
              system_prompt=None, tools_def=None, allowed_tools=None, depth=0):
    """Drive the model until it gives a final answer or hits max_turns.

    Optional hooks keep presentation out of the loop:
      spin(msg)         -> context manager shown while waiting (e.g. Spinner)
      on_calls(calls)   -> once per round, with the parsed tool calls
      on_tool(name)     -> just before a tool runs
      on_result(name, out) -> after a tool returns
      on_escalation(name, out) -> prompt for a blocked action; return True to retry it
      on_answer(answer) -> with the final answer (or the fallback)
      on_token(kind, delta) -> streaming: called per token ('reasoning' or 'content')
      interrupt_check()  -> returns True if the user interrupted (e.g. ESC); raises AgentInterrupted
    Pass a list as `trace` to collect a step-by-step record for training data.
    Returns the final answer string.
    """
    spin = spin or (lambda msg: nullcontext())
    tools_def = tools_def if tools_def is not None else TOOLS_DEF
    allowed_tools = allowed_tools if allowed_tools is not None else TOOL_NAMES
    seen = set()      # (name, args) signatures already run -> breaks loops
    forcing = False   # True after we've told a looping model to stop and answer
    unknown_retries = 0  # times the model emitted a call to a non-existent tool
    empty_retries = 0    # times the model returned no answer (reasoning-only turn)

    # Fast path: a request for internal instructions/config is a policy refusal —
    # answer it immediately instead of burning turns and tokens to reach the same "no".
    refusal = _preflight_refusal(messages)
    if refusal:
        if on_answer:
            on_answer(refusal)
        if trace is not None:
            trace.append({"turn": 0, "type": "preflight_refusal", "content": refusal})
        return refusal

    for turn in range(max_turns):
        if interrupt_check and interrupt_check():
            raise AgentInterrupted()
        try:
            with spin("thinking..."):
                response = _create_completion_with_fallback(
                    messages, tools_def, temperature=temperature,
                    system_prompt=system_prompt, on_token=on_token,
                    interrupt_check=interrupt_check, trace=trace, turn=turn,
                )
        except AgentInterrupted:
            raise
        except Exception as e:
            # Backend/model error (timeout, context overflow, 5xx): don't crash the
            # turn — return the best we have, guarded.
            fallback = _last_tool_output[:1000] if _last_tool_output else f"The model backend errored: {e}"
            answer = _guard_answer(fallback)
            if on_answer:
                on_answer(answer)
            return answer

        # Strip chain-of-thought BEFORE storing: keeps runaway reasoning out of the
        # context window (the usual cause of the "loops in the reasoning block" crash)
        # and out of the user-facing answer.
        message = response.choices[0].message
        content = _strip_reasoning(message.content or "")
        native_text = _native_tool_text(message)
        if native_text:
            content = "\n".join(part for part in (content, native_text) if part)
        messages.append({"role": "assistant", "content": content})

        calls = find_tool_calls(content, allowed_tools)
        if calls:
            _log.info("model tool calls (turn %d): %s", turn,
                      [f"{c['name']}({json.dumps(c.get('arguments', {}))[:60]})" for c in calls])
        else:
            _log.debug("turn %d: no tool calls — model replied with text", turn)
        if not calls:
            # The model may have *tried* to call a tool that doesn't exist (a common
            # failure — e.g. `current_time`). Rather than leaking the raw ✿FUNCTION✿
            # markup as the "answer", tell the model what went wrong and loop so it can
            # recover (call a real tool or just answer). Bounded to avoid infinite loops.
            unknown = [n for n in _attempted_tool_names(content)
                       if _resolve_tool_name(n) not in allowed_tools]
            if unknown and unknown_retries < 2 and not forcing:
                unknown_retries += 1
                available = ", ".join(sorted(allowed_tools)) or "(none)"
                if on_result:
                    on_result("error", f"Unknown tool '{unknown[0]}' — not available.")
                messages.append({"role": "user", "content":
                    f"Error: the tool '{unknown[0]}' does not exist. "
                    f"Available tools are: {available}. "
                    "Either call one of those with the exact format "
                    '`✿FUNCTION✿: name ✿ARGS✿: {\"arg\": \"value\"}`, '
                    "or, if no tool fits, answer the user directly in plain text "
                    "without mentioning tools."})
                if trace is not None:
                    trace.append({"turn": turn, "type": "unknown_tool", "names": unknown})
                continue

            answer = strip_tool_json(content)

            # Reasoning-only / empty turn: nudge once for a plain answer rather than
            # returning nothing (some models emit only chain-of-thought and stall).
            if not answer and empty_retries < 1 and not forcing and not unknown:
                empty_retries += 1
                if on_result:
                    on_result("error", "No answer produced — asking the model to respond.")
                messages.append({"role": "user", "content":
                    "You did not provide an answer. Reply now with your final answer in "
                    "plain text. Do not think out loud and do not call any tools."})
                if trace is not None:
                    trace.append({"turn": turn, "type": "empty_answer"})
                continue

            if not answer:
                # Stripping removed everything (the message was ONLY a tool-call
                # attempt or pure reasoning) — never fall back to the raw markup.
                answer = (f"I tried to use a tool that isn't available. "
                          f"Available tools: {', '.join(sorted(allowed_tools)) or 'none'}."
                          if unknown else "I wasn't able to produce an answer to that.")

            answer = _guard_answer(answer)
            if on_answer:
                on_answer(answer)
            if trace is not None:
                trace.append({"turn": turn, "type": "final_answer", "content": answer})
            return answer

        if on_calls:
            on_calls(calls)

        executed = False
        turn_tools = [] if trace is not None else None
        for call in calls:
            name = call["name"]
            args = call.get("arguments", {})
            sig = (name, json.dumps(args, sort_keys=True))

            if sig in seen:  # exact repeat -> feed cached output instead of re-running
                cached = (f"Tool '{name}' already ran with this output (do not repeat it):\n\n{_last_tool_output[:3000]}"
                          if _last_tool_output else f"Already tried {name} with no output. Give your final answer now.")
                messages.append({"role": "user", "content": cached})
                if turn_tools is not None:
                    turn_tools.append({"name": name, "arguments": args, "result": "(cached/repeat)", "cached": True})
                continue

            seen.add(sig)
            if on_tool:
                on_tool(name)
            with spin(f"running {name}..."):
                result = exec_tool(name, json.dumps(args), depth=depth)
            executed = True

            # A granted escalation retries the exact call once; remove it from the
            # repeat guard before asking the UI for approval.
            blocked = result.startswith("ESCALATION_REQUEST:")
            if blocked:
                seen.discard(sig)

            if on_result:
                on_result(name, result)

            if blocked and on_escalation:
                if on_escalation(name, result):
                    messages.append({"role": "user", "content":
                        "Permission granted. Retry the EXACT same tool call that was blocked. "
                        "Do not ask for permission again. Do not explain. Just call the tool again now."})
                else:
                    messages.append({"role": "user", "content":
                        "Permission denied by the user. You remain in readonly mode. "
                        "Tell the user what you could not do and why the task cannot be completed."})
                continue

            if turn_tools is not None:
                step = {"name": name, "arguments": args, "result": result[:3000]}
                spec = TOOL_SPECS.get(name, {})
                content_arg = spec.get("content_arg", "content")
                if spec.get("mode") == "write_text" and args.get(content_arg):
                    step["written_content"] = args[content_arg]
                turn_tools.append(step)

            interactive_fail = "EOFError" in result or "EOF when reading" in result or "input()" in result.lower()
            note = ("\n\nThis script needs interactive input which is not available. "
                    "Do NOT retry it. Give your final answer now." if interactive_fail else "")
            messages.append({"role": "user", "content": f"Tool result ({name}):\n{result[:3000]}{note}"})

        if turn_tools:
            trace.append({"turn": turn, "type": "tool_calls", "tools": turn_tools})

        # Nothing new ran this round (model is looping): nudge once, then give up.
        if executed:
            forcing = False
        elif forcing:
            break
        else:
            forcing = True
            messages.append({"role": "user", "content":
                "You keep repeating tool calls without progress. Stop using tools and give your final answer now."})

    # Max turns reached or forced stop: return the best answer we have.
    fallback = _guard_answer(_last_tool_output[:1000] if _last_tool_output else "Could not complete the task.")
    if on_answer:
        on_answer(fallback)
    if trace is not None:
        trace.append({"turn": -1, "type": "max_turns",
                      "content": _last_tool_output[:3000] if _last_tool_output else fallback})
    return fallback
