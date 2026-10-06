#!/bin/sh
# Seed the AGENT8088_HOME volume with the packaged default config.txt on
# first run, when the volume is empty. The setup wizard refuses to start
# without a config.txt at AGENT8088_HOME, and the engine's fallback to the
# packaged APP_DIR/config.txt only covers runtime — not the wizard.
set -e
HOME_DIR="${AGENT8088_HOME:-/home/a8088/.agent8088}"
PACKAGED_CONFIG="/app/src/agent8088/config.txt"
UID_NOW="$(id -u)"
GID_NOW="$(id -g)"

# A bind mount (-v ./data:/home/a8088/.agent8088) keeps the HOST directory's
# owner, which is usually not this container's uid. Everything after this --
# config, sessions, memory -- then fails with a bare "Permission denied".
if ! mkdir -p "$HOME_DIR" 2>/dev/null || [ ! -w "$HOME_DIR" ]; then
    echo "[agent8088] ERROR: $HOME_DIR is not writable by uid $UID_NOW (gid $GID_NOW)." >&2
    echo "  A bind-mounted host directory keeps its host owner. Fix one of:" >&2
    echo "    on the host:  sudo chown -R $UID_NOW:$GID_NOW <host-dir>" >&2
    echo "    or use the named volume from docker-compose.yml (agent8088-data)" >&2
    echo "    or run as the directory's owner:  docker run --user \"\$(id -u):\$(id -g)\" ..." >&2
    exit 1
fi

# The Docker socket is only needed for sandbox_backend=docker, so this warns
# rather than failing. The socket is owned by the host's docker group, whose
# gid this image cannot know in advance.
DOCKER_SOCK="/var/run/docker.sock"
if [ -S "$DOCKER_SOCK" ] && { [ ! -r "$DOCKER_SOCK" ] || [ ! -w "$DOCKER_SOCK" ]; }; then
    SOCK_GID="$(stat -c %g "$DOCKER_SOCK" 2>/dev/null || echo "<gid>")"
    echo "[agent8088] WARNING: $DOCKER_SOCK is mounted but uid $UID_NOW cannot use it (EACCES)." >&2
    echo "  The docker sandbox backend will fail. Give the container the socket's group:" >&2
    echo "    docker-compose.yml:  group_add: [\"$SOCK_GID\"]" >&2
    echo "    docker run:          --group-add $SOCK_GID" >&2
fi

if [ ! -f "$HOME_DIR/config.txt" ] && [ -f "$PACKAGED_CONFIG" ]; then
    cp "$PACKAGED_CONFIG" "$HOME_DIR/config.txt"
    echo "[agent8088] Seeded default config.txt into $HOME_DIR"
fi

exec agent8088 "$@"
