#!/usr/bin/env bash
# PostToolUse hook (Edit|Write): run the unit suite after edits to
# src/agent8088/**/*.py. The suite is ~8s for 349 tests, fast enough to run
# on every edit — this would have caught the _wrap_untrusted regression
# immediately instead of needing a manual pytest run after the fact.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

INPUT="$(cat)"
FILE_PATH="$(printf '%s' "$INPUT" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
    ti = d.get("tool_input", {}) or {}
    tr = d.get("tool_response", {}) or {}
    print(tr.get("filePath") or ti.get("file_path") or "")
except Exception:
    print("")
' 2>/dev/null || true)"

case "$FILE_PATH" in
    "$REPO_ROOT"/src/agent8088/*.py) ;;
    *) exit 0 ;;
esac

cd "$REPO_ROOT"
PYTHON="$REPO_ROOT/.venv/bin/python"
[ -x "$PYTHON" ] || PYTHON="python3"

set +e
RESULT="$(AGENT8088_CONFIG=/nonexistent "$PYTHON" -m pytest tests/ -q 2>&1)"
STATUS=$?
set -e

if [ "$STATUS" -ne 0 ]; then
    SUMMARY="$(printf '%s' "$RESULT" | tail -15)"
    python3 -c '
import json, sys
print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        "additionalContext": "pytest failed after editing " + sys.argv[1] + ":\n\n" + sys.argv[2],
    }
}))
' "$FILE_PATH" "$SUMMARY"
fi

exit 0
