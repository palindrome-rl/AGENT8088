# Commit messages

Do not append a `Co-Authored-By: Claude ...` trailer to commit messages in this repo. Commit as the human author only.

# Development memory

Use TencentDB Agent Memory only as optional development context for this repository.

- Proxy: `http://127.0.0.2:8096`
- Team: `team-example`
- Shared agent: `Agent8088 Developer` (`agent-example`)
- Search the shared Wiki and CodeGraph for relevant architecture, decisions, conventions, and code relationships before implementation.
- Current code, tests, and explicit user requirements are authoritative; verify shared memory because it may be stale.
- Configure access with each developer's personal key outside the repository. Never commit keys, tokens, passwords, `.env` values, production data, customer data, or personal data.
- This integration is development-only. Do not make application/runtime code depend on the memory server, send production traffic to it, or alter production deployment behavior.
- Use only team-shared development assets; do not expose private or personal memories.
- If the LAN server is unavailable, continue without shared context and report it.

