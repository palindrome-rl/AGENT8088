#!/usr/bin/env bash
# PreToolUse hook (Bash): block bare `agent8088` / `python -m agent8088` invocations
# that don't isolate HOME/AGENT8088_CONFIG/AGENT8088_HOME.
#
# Why: running the CLI without an isolated HOME triggers real, one-time side
# effects against the developer's actual ~/.agent8088/config.txt (e.g. the
# api_key -> .env migration). That happened by accident once already.
set -euo pipefail

INPUT="$(cat)"
CMD="$(printf '%s' "$INPUT" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
    print(d.get("tool_input", {}).get("command", ""))
except Exception:
    print("")
' 2>/dev/null || true)"

# Does this command INVOKE agent8088 (as the command itself, right after a
# shell separator) — not just mention it, e.g. as a grep pattern or arg to
# another program?
if ! printf '%s' "$CMD" | grep -qE '(^|[;&|])[[:space:]]*(agent8088|python3?[[:space:]]+-m[[:space:]]+agent8088(\.cli)?)([[:space:]]|$)'; then
    exit 0
fi

# Already isolated (inline prefix, export, or `env`)?
if printf '%s' "$CMD" | grep -qE '(^|[;&|]|[[:space:]])(export[[:space:]]+)?(HOME|AGENT8088_CONFIG|AGENT8088_HOME)='; then
    exit 0
fi
if printf '%s' "$CMD" | grep -qE '(^|[;&|]|[[:space:]])env[[:space:]]+.*(HOME|AGENT8088_CONFIG|AGENT8088_HOME)='; then
    exit 0
fi

cat <<'EOF'
{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"This agent8088 invocation has no isolated HOME/AGENT8088_CONFIG/AGENT8088_HOME, so it can touch the developer's real ~/.agent8088 config (this has happened before). Prefix the command, e.g.: HOME=$SANDBOX AGENT8088_CONFIG=/nonexistent <command> — or set AGENT8088_HOME to a temp dir. If hitting the real config is genuinely intended, ask the user first."}}
EOF
