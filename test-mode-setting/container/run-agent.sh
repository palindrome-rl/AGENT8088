#!/bin/bash
# Runs inside the Terminal-Bench task container. Uploaded by the Harbor adapter
# to /opt/agent8088/test-mode-setting/container/run-agent.sh and invoked once per trial.
#
#   run-agent.sh <instruction-file> [budget-seconds]
#
# The budget is the task's agent timeout minus a margin. Harbor stops waiting
# at its own timeout but may leave the process running while the verifier
# grades, so the watchdog here makes sure agent8088 has exited by then.
set -u

instruction_file="$1"
budget="${2:-0}"
log=/logs/agent/agent8088.txt
mkdir -p /logs/agent "$AGENT8088_HOME"

cmd=(/opt/agent8088/bin/agent8088 --prompt-file "$instruction_file")
cd "${AGENT8088_WORKDIR:-/app}" 2>/dev/null || cd /

if [ "$budget" -gt 0 ] 2>/dev/null; then
    timeout -k 15 "$budget" "${cmd[@]}" < /dev/null > "$log" 2>&1
    rc=$?
else
    "${cmd[@]}" < /dev/null > "$log" 2>&1
    rc=$?
fi

tail -n 40 "$log"
if [ "$rc" -eq 124 ] || [ "$rc" -eq 137 ]; then
    # Budget reached: the grader still runs on whatever was produced.
    echo "[agent8088] stopped at the ${budget}s budget"
    exit 0
fi
exit "$rc"
