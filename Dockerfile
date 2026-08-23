# Agent8088 production Docker image.
# Run the agent in a container without a system-wide install:
#   docker compose run --rm agent8088              # interactive REPL
#   docker compose up -d gateway                   # messaging gateway
FROM python:3.11-slim

# Build deps for transitive wheels (Pillow, playwright, ddgs/primp),
# git for the git_* tools, nodejs+npm for the WhatsApp bridge, curl for
# in-container endpoint probes, and the shared libs Playwright's Chromium
# needs at runtime.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential curl git ca-certificates nodejs npm \
        libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
        libdrm2 libdbus-1-3 libxkbcommon0 libxcomposite1 libxdamage1 \
        libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install the package with gateway extras. dev (pytest/ruff) is not needed
# in production.
COPY pyproject.toml README.md ./
COPY src ./src
COPY assets ./assets
RUN pip install --no-cache-dir -e ".[gateway]"

# docker-ce-cli so run_sandboxed (sandbox_backend=docker) can shell out to
# `docker run` via the mounted host socket. CLI only, no daemon.
RUN install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg \
       -o /etc/apt/keyrings/docker.asc \
    && chmod a+r /etc/apt/keyrings/docker.asc \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
       > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends docker-ce-cli \
    && rm -rf /var/lib/apt/lists/*

# Playwright Chromium for browse_page.
RUN playwright install chromium

# WhatsApp bridge deps (Baileys/express). The bridge ships in the wheel
# (pyproject force-include) but its node_modules do not, so install them here.
RUN cd src/agent8088/gateway/platforms/whatsapp_bridge && npm install --omit=dev

# Entrypoint: seed the volume with the packaged default config.txt on first
# run, then exec agent8088. The setup wizard refuses to start without a
# config.txt at AGENT8088_HOME, and the engine's fallback to APP_DIR/config.txt
# only covers runtime, not the wizard.
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Non-root user: the agent's file-tool permission layer checks write targets,
# and running as root would bypass real-world write-refusal tests.
RUN useradd -m a8088 && chown -R a8088:a8088 /app \
    && mkdir -p /home/a8088/.agent8088 && chown a8088:a8088 /home/a8088/.agent8088
USER a8088

# The agent8088 binary is on PATH via the pip install.
# AGENT8088_HOME points at the mounted volume so config and sessions persist.
ENV AGENT8088_HOME=/home/a8088/.agent8088
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]