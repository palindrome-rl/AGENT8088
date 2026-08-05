# Skills & Sub-agents

[← Wiki index](README.md)

Two different extension mechanisms that are easy to confuse:

| | Skills | Sub-agents |
|---|---|---|
| What it is | Packaged knowledge + extra tools | A separate agent run with its own context |
| Cost | Text in the prompt | A whole nested agent loop |
| Use when | The agent needs to *know* something | You want work done *without* polluting context |
| Configured in | `skills_dir` packages | `agents_dir` markdown profiles |

---

## Sub-agents

`spawn_subagent(agent_type, task)` runs a nested agent with its own
conversation, its own turn budget, and a **restricted tool set**. The parent
gets back only the final answer — intermediate steps never enter its context.

### The 4 bundled profiles

| Profile | Tools | Max turns | For |
|---|---|---|---|
| `explore` | `execute_shell`, `read_text`, `web_search`, `get_page_title`, `last_output` | 6 | Read-only codebase search. No write tool at all. |
| `researcher` | `web_search`, `get_page_title`, `read_text`, `last_output` | 8 | Web research with citations. No shell. |
| `coder` | `execute_shell`, `read_text`, `write_file`, `last_output` | 10 | Write code and verify it runs. |
| `general-purpose` | the above plus `calculate` | 8 | Mixed multi-step work. |

Note the tool restriction is real isolation, not advice: `explore` has no
`write_file`, so an explore sub-agent physically cannot write, whatever the
model decides.

### Defining your own

Markdown with YAML frontmatter in `agents_dir`:

```markdown
---
name: reviewer
description: Reviews a diff for correctness and flags risky changes.
tools: read_text, execute_shell, last_output
max_turns: 8
---

You are a code reviewer. Read the diff, then report only defects you can
point at with a file and line. Do not restate what the code does.
```

The body becomes the sub-agent's system prompt.

### Guardrails

- **Depth-limited** — `subagent_max_depth` prevents a sub-agent spawning an
  infinite chain of sub-agents.
- **Permission layer still applies** — a sub-agent's `write_file` escalates to
  the same approval prompt as the parent's would.
- **Unknown profile falls back** to `default_subagent` rather than erroring.
- **Tool set is intersected** — a profile can only narrow the available tools,
  never grant something the parent didn't have.

### From the REPL

```
/agents                 # list profiles
/agent explore <task>   # run one directly
```

---

## Skills

A skill package bundles instructions, and optionally extra tool definitions,
that get merged into the agent's context.

### The 5 bundled skills

| Skill | Category |
|---|---|
| `plan` | workflow |
| `systematic-debugging` | workflow |
| `test-driven-development` | workflow |
| `github-code-review` | workflow |
| `documentation-writing` | workflow |

Loaded skills appear in the system prompt under `## Installed skills`, and in
`/status`.

### Managing them

```
/skills                    # list, with enabled/disabled state
/skills disable plan       # turn one off for this session
/skills enable plan
```

Disabled state is saved with a named session, so `/resume` restores it.

### Writing a skill

A directory in `skills_dir` containing `SKILL.md`:

```markdown
---
name: my-skill
description: What this is for and when to use it.
category: workflow
---

Instructions the agent should follow when this skill applies.
```

A skill may also declare extra tools, which are merged into the registry —
**but skill tools cannot override core tools.** A skill declaring `write_file`
does not get to replace the real one. A directory without `SKILL.md` is skipped
rather than erroring.

---

## SkillOpt

Agent8088 can improve its own skill text through text-space optimisation:
run a skill, score the outcome, rewrite the instructions, repeat. This is
"self-improving" in the prompt-engineering sense — it edits skill markdown, not
model weights. See the SkillOpt section in the top-level `README.md`.

---

## Persona — `USER.md`

`USER.md` is a plain markdown file describing you, injected into the prompt so
the agent has standing context ("I work in Python", "prefer terse answers").

Two properties worth knowing:

- **Frontmatter is dropped** — only the body is used.
- **It's framed as data, not instructions.** Content in `USER.md` is presented
  as facts about the user, so it can't be used to issue commands that bypass the
  permission layer.

An empty or missing `USER.md` adds nothing — it's entirely optional.
