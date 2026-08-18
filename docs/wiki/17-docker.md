# Docker

[← Wiki index](README.md)

Run Agent8088 in a container — no system-wide install, no `pip`, no `uv`. The
image ships with every dependency pre-installed: the agent, all gateway
adapters, Playwright Chromium, the WhatsApp bridge, and the Docker CLI for
`run_sandboxed`.

## Quick start

```sh
cd /path/to/Agent8088-Features-added        # docker-compose.yml lives here

docker compose run --rm agent8088 --setup    # first-time config wizard
docker compose run --rm agent8088            # interactive REPL
docker compose up -d gateway                 # messaging gateway (background)
```

That's it. Config, API keys, sessions and memory persist in a named Docker
volume — they survive container removal.

## What the image contains

| Component | Included |
|---|---|
| `agent8088` CLI | yes — on PATH via `pip install -e` |
| All 21 tools | yes |
| Gateway extras (Slack/Discord/Telegram/Email) | yes |
| Playwright Chromium (`browse_page`) | yes |
| WhatsApp bridge (Baileys/express) | yes |
| `docker-ce-cli` (for `run_sandboxed` with `sandbox_backend=docker`) | yes |
| `dev` extras (pytest, ruff) | **no** — not needed in production |

The image runs as a non-root user (`a8088`), so the agent's file-permission
layer behaves the same as a real install — writes to `/root` are refused.

## First-time setup

The volume starts empty. The entrypoint script seeds it with the packaged
default `config.txt` on first run, so `--setup` works immediately:

```sh
docker compose run --rm agent8088 --setup
```

You'll see:

```
[agent8088] Seeded default config.txt into /home/a8088/.agent8088
Agent8088 setup

? Working directory: .
? Select model provider: ...
? API key: ...
? Select model: ...
```

The wizard writes to the volume. Run it once; after that, `--gateway` works
headless.

## Interactive REPL

```sh
docker compose run --rm agent8088
```

`--rm` removes the container on exit — your config and sessions are in the
volume, not the container, so nothing is lost.

## Messaging gateway

```sh
docker compose up -d gateway       # start in background
docker compose logs -f gateway     # watch logs
docker compose down                # stop and remove
```

The gateway service has `restart: unless-stopped`, so it survives a Docker
daemon restart.

## Where state lives

Everything persists in the `agent8088-data` named volume, mounted at
`/home/a8088/.agent8088` inside the container:

| Path (in container) | What |
|---|---|
| `/home/a8088/.agent8088/config.txt` | Settings |
| `/home/a8088/.agent8088/.env` | API keys and gateway tokens |
| `/home/a8088/.agent8088/gateway-sessions/` | Per-chat gateway history |
| `/home/a8088/.agent8088/memory.db` | Persistent memory (SQLite) |

Inspect or back it up:

```sh
docker volume inspect agent8088-features-added-development_agent8088-data
```

## Starting fresh

Wipe the volume and reconfigure from scratch:

```sh
docker compose down -v            # -v removes the named volume
docker compose run --rm agent8088 --setup
```

## Docker socket mount

Both services mount `/var/run/docker.sock` so `run_sandboxed` can spawn sibling
containers on the host (when `sandbox_backend=docker`). If the agent stays in
readonly mode and you don't need sandboxed code execution, comment out the
socket mount in `docker-compose.yml`:

```yaml
volumes:
  - agent8088-data:/home/a8088/.agent8088
  # - /var/run/docker.sock:/var/run/docker.sock   # omit if readonly only
```

## What the Dockerfile does not include

- **`dev` extras** (pytest, ruff, pip-audit) — not needed to run the agent.
  If you want to run the test suite in a container, install them:
  `docker compose run --rm --entrypoint bash agent8088 -c "pip install -e \".[dev]\" && pytest tests/ -q"`

- **An Ollama server** — the default config points at `localhost:11434`, which
  is the host's Ollama from inside the container only if you set
  `provider.ollama.base_url=http://host.docker.internal:11434/v1` in
  `config.txt`. Alternatively, point at any remote endpoint during `--setup`.

- **A SearXNG instance** — `/search setup` provisions one in a sibling
  container if Docker is available. With the socket mounted, that works from
  inside the agent container too. Otherwise the keyless `ddgs` fallback serves
  web search with no setup.

## Building from source

The image builds from the repo root:

```sh
docker compose build
```

Or without Compose:

```sh
docker build -t agent8088 .
```

The `.dockerignore` excludes `.venv`, `.git`, `tests/`, `docs/`, `artifacts/`
and `research/` — only the source, `pyproject.toml`, `README.md` and `assets/`
go into the image.

## Troubleshooting

### `no configuration file provided: not found`

You're not in the directory containing `docker-compose.yml`. `cd` into the
repo root first, or use `-f`:

```sh
docker compose -f /path/to/docker-compose.yml run --rm agent8088 --setup
```

### `Config not found: /home/a8088/.agent8088/config.txt`

The entrypoint should seed it automatically. If it didn't, the volume may
have been created without the entrypoint running — remove it and try again:

```sh
docker compose down -v
docker compose run --rm agent8088 --setup
```

### Permission denied writing to the volume

The non-root user `a8088` owns the volume. If you see permission errors, the
volume was likely created before the `USER a8088` fix. Remove and recreate:

```sh
docker compose down -v
docker compose build
docker compose run --rm agent8088 --setup
```

See [Troubleshooting](13-troubleshooting.md) for general agent issues and
[Getting Started](01-getting-started.md) for the non-Docker install path.