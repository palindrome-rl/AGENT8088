# Skill Packages

Drop a directory here to add tools to Agent8088 — no code changes required.

```
skills_installed/
  weather/
    SKILL.md     # metadata + guidance (optional)
    tools.txt    # tool definitions (same format as the root tools.txt)
```

## SKILL.md

```markdown
---
name: weather
description: Weather lookups for any city
version: 1.0
---
Use get_weather when the user asks about current conditions or a forecast.
```

## tools.txt

```
get_weather|Get the forecast for a city|mode=http_get|args=city|url=https://wttr.in/{city}?format=3|timeout=15
```

Format: `name|description|key=value|key=value...`

Available package-tool modes: `shell`, `http_get`, `read_text`, `write_text`,
`python_eval`, `browser`, `docker`, `cron`, `subagent`, `plan`, and
`last_output`. The internal `skill` and `cli_anything` modes are reserved for
Agent8088 core tools and cannot be used to bypass their resource and permission
checks.

Set `progressive: true` in SKILL.md frontmatter for a large skill. Only its
name, description, and activation hint enter the initial prompt; the agent must
load `SKILL.md` and permitted text resources through `view_skill` when needed.

**Note:** `|` is the field separator — never use it inside a description.

## Verifying an install

```bash
agent8088
```

Then run `/skills` to list packages, and `/tools` to confirm the new tools loaded.

## Vendored skills

The lifecycle packages are vendored from [addyosmani/agent-skills](https://github.com/addyosmani/agent-skills)
(MIT), commit `6ca0cd7`; its license is retained in `UPSTREAM_LICENSE.md`.

The upstream browser-testing skill is deliberately excluded: Agent8088's existing
`browsing` skill now documents its faster tool-selection path instead.

Each upstream skill is progressive, so its workflow is injected only when the
task selector matches it. Shared upstream checklists live in `_references/` and
are available as `view_skill(..., resource="shared/<file>")`.

## Safety

- A package **cannot override a core tool** (e.g. `execute_shell`) — core
  definitions always win, so a skill can't hijack existing behavior.
- All `http_get` and `browser` URLs still pass the SSRF guard.
- **Review a package's `tools.txt` before installing it.** A skill can define
  `mode=shell` tools, which run real commands on your machine. Treat an untrusted
  skill package the same way you would treat an untrusted shell script.
