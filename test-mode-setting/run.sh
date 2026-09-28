#!/bin/bash
# Run agent8088 on Terminal-Bench 2.1 tasks with Harbor, one task at a time
# (vLLM runs with --max-num-seqs 1).
#
#   test-mode-setting/run.sh --tb /path/to/terminal-bench-2-1 --wheel dist/agent8088-*.whl \
#       [--base-url http://host.docker.internal:8000/v1] [--model qwen3.8-27b] \
#       [--tasks test-mode-setting/tasks.txt] [--attempts 1] [--jobs-dir jobs]
#
# Each task gets its own `harbor run` so the in-container watchdog can use that
# task's agent timeout (task.toml) minus a margin.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
tb="" wheel="" base_url="http://host.docker.internal:8000/v1" model="qwen3.8-27b"
tasks="$here/tasks.txt" attempts=1 jobs_dir="jobs" margin=45
while [ $# -gt 0 ]; do
    case "$1" in
        --tb) tb="$2"; shift 2 ;;
        --wheel) wheel="$2"; shift 2 ;;
        --base-url) base_url="$2"; shift 2 ;;
        --model) model="$2"; shift 2 ;;
        --tasks) tasks="$2"; shift 2 ;;
        --attempts) attempts="$2"; shift 2 ;;
        --jobs-dir) jobs_dir="$2"; shift 2 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done
[ -d "$tb/tasks" ] || { echo "--tb must point at a terminal-bench-2-1 checkout" >&2; exit 2; }
[ -f "$wheel" ] || { echo "--wheel must point at a built agent8088 wheel" >&2; exit 2; }
wheel="$(cd "$(dirname "$wheel")" && pwd)/$(basename "$wheel")"

overlay=()
if [ "$(uname -s)" = "Linux" ]; then
    overlay=(--extra-docker-compose "$here/host-gateway.compose.yaml")
fi

agent_timeout() {
    python3 -c 'import sys, tomllib; print(int(tomllib.load(open(sys.argv[1], "rb"))["agent"]["timeout_sec"]))' \
        "$tb/tasks/$1/task.toml"
}

grep -v -E '^\s*(#|$)' "$tasks" | while read -r task; do
    budget=$(( $(agent_timeout "$task") - margin ))
    echo "=== $task (budget ${budget}s)"
    PYTHONPATH="$here${PYTHONPATH:+:$PYTHONPATH}" harbor run \
        -p "$tb/tasks" -i "$task" \
        -a agent8088_harbor.agent:Agent8088 -m "openai/$model" \
        --ak "wheel_path=$wheel" --ak "base_url=$base_url" --ak "budget_sec=$budget" \
        --ae "OPENAI_API_KEY=${OPENAI_API_KEY:-EMPTY}" \
        -e docker -n 1 -k "$attempts" -o "$jobs_dir" --job-name "agent8088-$task-$(date +%Y%m%d-%H%M%S)" \
        ${overlay[@]+"${overlay[@]}"} < /dev/null || echo "harbor run failed for $task" >&2
done
