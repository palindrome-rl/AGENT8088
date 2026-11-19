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

# `timeout` sends SIGTERM at the budget, then SIGKILL 15 s later; agent8088
# saves its trace and transcript on SIGTERM and exits within that grace.
started=$(date +%s)
if [ "$budget" -gt 0 ] 2>/dev/null; then
    timeout -k 15 "$budget" "${cmd[@]}" < /dev/null > "$log" 2>&1
    rc=$?
else
    "${cmd[@]}" < /dev/null > "$log" 2>&1
    rc=$?
fi
elapsed=$(( $(date +%s) - started ))
# agent8088's own log (compaction, warnings, memory errors) lives under
# $AGENT8088_HOME, which Harbor does not collect; copy it next to the trace.
if [ -d "$AGENT8088_HOME/logs" ]; then
    mkdir -p /logs/agent/agent8088-internal-logs
    cp -r "$AGENT8088_HOME/logs/." /logs/agent/agent8088-internal-logs/ 2>/dev/null || true
fi
# agent8088's last line says why the run ended (answered, time_budget,
# turn_limit, no_progress, model_error, interrupted, watchdog, unknown).
end_reason=$(grep -a -o '\[agent8088\] run ended: [a-z_]*' "$log" | tail -n 1 | sed 's/.*: //')
stopped_by=none
if [ "$budget" -gt 0 ] 2>/dev/null && { [ "$rc" -eq 124 ] || [ "$rc" -eq 137 ]; }; then
    stopped_by=watchdog
    end_reason=watchdog
elif [ "$end_reason" = time_budget ]; then
    stopped_by=self        # the loop's own time limit (max_turn_seconds)
fi
# Exit record for the adapter and for later checks: a budget stop exits 0 (so
# Harbor still grades the work) and would otherwise look like a normal finish.
printf '{"exit_code": %d, "budget_sec": %d, "elapsed_sec": %d, "stopped_by": "%s", "end_reason": "%s"}\n' \
    "$rc" "${budget:-0}" "$elapsed" "$stopped_by" "${end_reason:-unknown}" > /logs/agent/agent8088-exit.json

tail -n 40 "$log"
if [ "$stopped_by" != none ]; then
    # The adapter reads this line and reports AgentTimeoutError to Harbor.
    echo "[agent8088] stopped at the ${budget}s budget ($stopped_by)"
    exit 0
fi
exit "$rc"
