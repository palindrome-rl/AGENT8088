#!/bin/bash
# Run agent8088 on Terminal-Bench 2.1 tasks with Harbor, one task at a time
# (vLLM runs with --max-num-seqs 1).
#
#   bench/terminal-bench/run.sh --tb /path/to/terminal-bench-2-1 --wheel dist/agent8088-*.whl \
#       [--base-url http://host.docker.internal:8000/v1] [--model qwen3.8-27b] \
#       [--tasks bench/terminal-bench/tasks.txt] [--attempts 1] [--jobs-dir jobs] \
#       [--extra-compose thunder-host-network.compose.yaml]
#
# Each task gets its own `harbor run` so the in-container watchdog can use that
# task's agent timeout (task.toml) minus a margin. Exits non-zero if any
# `harbor run` failed (the remaining tasks still run).
set -euo pipefail

# Docker socket access: this shell may predate the user's `docker` group
# membership (a group change applies only to new logins), in which case Harbor
# fails later with "Docker daemon is not running". Re-exec under `sg docker`
# once; the second pass carries the group, so this cannot loop.
if [ "$(uname -s)" = "Linux" ] && ! docker info >/dev/null 2>&1; then
    if ! id -nG 2>/dev/null | grep -qw docker \
        && command -v sg >/dev/null 2>&1 && getent group docker >/dev/null 2>&1; then
        echo "run.sh: docker socket not accessible in this shell; re-exec via 'sg docker'" >&2
        exec sg docker -c "$(printf '%q ' "$0" "$@")"
    fi
    echo "run.sh: cannot reach the Docker daemon (is it running? is this user in the 'docker' group?)" >&2
    exit 3
fi

here="$(cd "$(dirname "$0")" && pwd)"
tb="" wheel="" base_url="http://host.docker.internal:8000/v1" model="qwen3.8-27b"
# margin: the watchdog stops agent8088 this long before Harbor's own timeout,
# so it has exited (SIGTERM, +15 s SIGKILL grace) before Harbor gives up and
# grades. Small, so the agent gets nearly the task's full time.
tasks="$here/tasks.txt" attempts=1 jobs_dir="jobs" margin=20 extra_compose=()
while [ $# -gt 0 ]; do
    case "$1" in
        --tb) tb="$2"; shift 2 ;;
        --wheel) wheel="$2"; shift 2 ;;
        --base-url) base_url="$2"; shift 2 ;;
        --model) model="$2"; shift 2 ;;
        --tasks) tasks="$2"; shift 2 ;;
        --attempts) attempts="$2"; shift 2 ;;
        --jobs-dir) jobs_dir="$2"; shift 2 ;;
        --extra-compose) extra_compose+=("$2"); shift 2 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done
[ -d "$tb/tasks" ] || { echo "--tb must point at a terminal-bench-2-1 checkout" >&2; exit 2; }
[ -f "$wheel" ] || { echo "--wheel must point at a built agent8088 wheel" >&2; exit 2; }
wheel="$(cd "$(dirname "$wheel")" && pwd)/$(basename "$wheel")"

overlay=()
if [ "${#extra_compose[@]}" -gt 0 ]; then
    # Explicit overlays win over the portability default.
    for compose_file in "${extra_compose[@]}"; do
        overlay+=(--extra-docker-compose "$compose_file")
    done
elif [ "$(uname -s)" = "Linux" ]; then
    overlay=(--extra-docker-compose "$here/host-gateway.compose.yaml")
fi

agent_timeout() {
    python3 -c 'import sys, tomllib; print(int(tomllib.load(open(sys.argv[1], "rb"))["agent"]["timeout_sec"]))' \
        "$tb/tasks/$1/task.toml"
}

failed=()
while read -r task; do
    budget=$(( $(agent_timeout "$task") - margin ))
    echo "=== $task (budget ${budget}s)"
    PYTHONPATH="$here${PYTHONPATH:+:$PYTHONPATH}" harbor run \
        -p "$tb/tasks" -i "$task" \
        -a agent8088_harbor.agent:Agent8088 -m "openai/$model" \
        --ak "wheel_path=$wheel" --ak "base_url=$base_url" --ak "budget_sec=$budget" \
        --ae "OPENAI_API_KEY=${OPENAI_API_KEY:-EMPTY}" \
        -e docker -n 1 -k "$attempts" -o "$jobs_dir" --job-name "agent8088-$task-$(date +%Y%m%d-%H%M%S)" \
        ${overlay[@]+"${overlay[@]}"} < /dev/null \
        || { echo "harbor run failed for $task (rc=$?)" >&2; failed+=("$task"); }
done < <(grep -v -E '^\s*(#|$)' "$tasks")
if [ "${#failed[@]}" -gt 0 ]; then
    echo "harbor run failed for ${#failed[@]} task(s): ${failed[*]}" >&2
    exit 1
fi
