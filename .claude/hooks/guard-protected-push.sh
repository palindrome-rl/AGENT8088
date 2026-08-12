#!/usr/bin/env bash
# PreToolUse hook (Bash): hard-block `git push` that would land on main or
# development. Mechanical backstop for "never push to main/development
# without the user's explicit go-ahead in chat" — a hook has no way to know
# whether a command was user-typed vs. model-issued, so this blocks
# unconditionally from the Bash tool. To actually push to one of these
# branches, run the push yourself in your own terminal.
set -euo pipefail

PROTECTED='^(main|development)$'

INPUT="$(cat)"
CMD="$(printf '%s' "$INPUT" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
    print(d.get("tool_input", {}).get("command", ""))
except Exception:
    print("")
' 2>/dev/null || true)"

# Is this a `git push` invocation at all?
if ! printf '%s' "$CMD" | grep -qE '(^|[;&|])[[:space:]]*git[[:space:]]+push([[:space:]]|$)'; then
    exit 0
fi

TARGET_LINE="$(printf '%s' "$CMD" | grep -oE '(^|[;&|])[[:space:]]*git[[:space:]]+push[^;&|]*' | tail -1)"

# Explicit refspec targeting a protected branch, e.g. `git push origin main`,
# `git push origin HEAD:development`, `git push origin main:main`.
if printf '%s' "$TARGET_LINE" | grep -qE '(^|[[:space:]]|:)(main|development)([[:space:]]|:|$)'; then
    BLOCK=1
else
    BLOCK=0
fi

# No explicit branch named -> pushes whatever the current branch tracks.
# Block if that's a protected branch.
if [ "$BLOCK" -eq 0 ] && printf '%s' "$TARGET_LINE" | grep -qE '^[[:space:]]*git[[:space:]]+push[[:space:]]*($|--[a-zA-Z-]+([[:space:]]|$))*[[:space:]]*$'; then
    CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
    if printf '%s' "$CURRENT_BRANCH" | grep -qE "$PROTECTED"; then
        BLOCK=1
    fi
fi

if [ "$BLOCK" -eq 1 ]; then
    cat <<'EOF'
{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"Blocked: this push targets a protected branch (main or development). Pushes to these branches must be run by the user directly in their own terminal, not through Claude Code's Bash tool — this is a hard mechanical gate, not something to work around from here."}}
EOF
fi
