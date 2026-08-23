#!/bin/sh
# Seed the AGENT8088_HOME volume with the packaged default config.txt on
# first run, when the volume is empty. The setup wizard refuses to start
# without a config.txt at AGENT8088_HOME, and the engine's fallback to the
# packaged APP_DIR/config.txt only covers runtime — not the wizard.
set -e
HOME_DIR="${AGENT8088_HOME:-/home/a8088/.agent8088}"
PACKAGED_CONFIG="/app/src/agent8088/config.txt"

if [ ! -f "$HOME_DIR/config.txt" ] && [ -f "$PACKAGED_CONFIG" ]; then
    cp "$PACKAGED_CONFIG" "$HOME_DIR/config.txt"
    echo "[agent8088] Seeded default config.txt into $HOME_DIR"
fi

exec agent8088 "$@"