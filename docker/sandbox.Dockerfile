# The image the Docker sandbox fallback runs commands in: Python, Node and git,
# so pip, npm and git clone all work in one container. Published as
# ghcr.io/palindrome-rl/agent8088-sandbox by .github/workflows/publish-image.yml.
#
# The base is pinned by digest; bump it on purpose, then rebuild and run the
# sandbox tests (tests/test_docker_sandbox_fixes.py, the Docker part of the
# attack suite).
FROM node:22-bookworm-slim@sha256:be979e5523c76fa4d5ca4ebd3099078f220aa47ec39c48ad5b5df9314debd6eb

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      python3 python3-venv python3-pip python-is-python3 git ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && rm -f /usr/lib/python3*/EXTERNALLY-MANAGED

# Commands may run as the host user's id (Linux), which has no home here.
ENV HOME=/tmp \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    npm_config_update_notifier=false

WORKDIR /workspace
CMD ["sh"]
