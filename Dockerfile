# Agent8088 E2E test image.
# python:3.11-slim matches the .venv used for local dev (3.11.x).
FROM python:3.11-slim

# Build deps for a few transitive wheels (Pillow, playwright, ddgs/primp),
# plus curl for in-container endpoint probes, git for the git_* tools,
# nodejs+npm for the WhatsApp bridge (whatsapp_enabled=1 spawns `node bridge.js`),
# and the shared libs Playwright's Chromium needs at runtime.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential curl git ca-certificates nodejs npm \
        libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
        libdrm2 libdbus-1-3 libxkbcommon0 libxcomposite1 libxdamage1 \
        libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install the package with the extras the E2E suite exercises.
# dev  -> pytest (in case we run unit tests inside too)
# gateway -> slack/discord/telegram/httpx imports resolve
COPY pyproject.toml README.md ./
COPY src ./src
COPY assets ./assets
RUN pip install --no-cache-dir -e ".[dev,gateway]"

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

# Playwright Chromium for browse_page. --with-deps would re-run apt; we
# installed the libs above already to keep one apt layer.
RUN playwright install chromium

# WhatsApp bridge deps (Baileys/express). The bridge ships in the wheel
# (pyproject force-include) but its node_modules do not, so install them here.
RUN cd src/agent8088/gateway/platforms/whatsapp_bridge && npm install --omit=dev

# Non-root user keeps file-tool permission tests honest (writes to /root
# should be refused). We still run as root for the Docker-socket mount
# permission, so this is informational only — left commented.
# RUN useradd -m a8088 && chown -R a8088:a8088 /app

# Default to a long sleep so `docker exec` can drive scenarios.
# The actual agent8088 binary is on PATH via the pip install.
ENTRYPOINT ["sleep", "infinity"]