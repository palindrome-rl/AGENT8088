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

### The 5 bundled profiles

| Profile | Tools | Max turns | For |
|---|---|---|---|
| `explore` | `execute_shell`, `read_text`, `web_search`, `get_page_title`, `last_output` | 6 | Read-only codebase search. No write tool at all. |
| `researcher` | `web_search`, `get_page_title`, `read_text`, `last_output` | 8 | Web research with citations. No shell. |
| `coder` | `execute_shell`, `read_text`, `write_file`, `last_output` | 10 | Write code and verify it runs. |
| `auditor` | `read_text`, `execute_shell`, `last_output` | 6 | Verify a completed step against the environment. Pinned readonly. |
| `general-purpose` | the above plus `calculate` | 8 | Mixed multi-step work. |

Note the tool restriction is real isolation, not advice: `explore` has no
`write_file`, so an explore sub-agent physically cannot write, whatever the
model decides.

### The permission floor

A profile may add `permission: readonly` to its frontmatter. The sub-run is then
pinned to readonly for its whole lifetime, whatever mode the caller was in —
including `full-auto`. `auditor` is the one bundled profile that uses it.

The floor only ever restricts. There is no frontmatter value that grants a
sub-agent more than the caller already had, so a profile cannot widen its own
permissions.

It also clears any grant the parent is holding — a one-shot y/n approval, or the
temporary grant `execute_plan` holds while running an approved plan. That part
matters more than it looks: without it, an auditor spawned in the middle of an
approved plan would be running inside the parent's write grant, and an agent
whose entire contract is "I only observe" could change the thing it was sent to
inspect.

And a pinned agent is **refused** a mutation rather than offered an escalation.
Sub-agent escalations do reach the user, so leaving them in place left "this agent
only observes" as a question someone could answer yes to — about the very file the
auditor was sent to look at. Only profiles that declare the floor are refused;
plain readonly mode still escalates, because that prompt *is* the approval flow.

This is why the auditor's read-only-ness is a property of the engine rather than
of its prompt. `check_permission()` refuses the write; the model is not being
asked to behave.

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
