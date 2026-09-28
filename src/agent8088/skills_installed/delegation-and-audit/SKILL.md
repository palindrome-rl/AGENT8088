---
name: delegation-and-audit
description: Delegate only independent bounded work to an appropriate sub-agent, then verify its result against the repository.
version: 1.0.0
category: harness
progressive: true
requires_tools: spawn_subagent
---

# Delegation and audit

Delegate only when the work is independent and the expected deliverable is concrete. Give the sub-agent a narrow task, relevant paths, and a completion check. Keep dependent edits in the parent task.

Treat a sub-agent report as a lead, not proof: inspect its diff or output and run the relevant verification before reporting completion. Use read-only exploration or audit profiles when mutation is unnecessary.
