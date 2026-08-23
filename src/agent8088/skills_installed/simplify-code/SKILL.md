---
name: simplify-code
description: Review recent code changes through four narrow lenses — reuse, quality, efficiency, altitude — then apply the safe fixes.
version: 1.0.0
category: software-development
---

Use this after a change is written and working, when asked to simplify, clean
up, or review recent work for over-engineering. It hunts complexity, not bugs —
use `github-code-review` for correctness.

## When NOT to use it

This runs four sub-agents, one after another. That is expensive. For a diff of
a few lines, apply the four lenses yourself in one pass instead — the value
here is fresh context per lens, which only matters when the diff is big enough
that one reviewer would lose track of it.

## 1. Capture the diff first

`git_diff` for unstaged work, or `git_log` then `execute_shell` with
`git diff <base>..HEAD` for a branch. Review only what changed. A reviewer
handed the whole codebase reports things nobody touched.

## 2. Run the four reviewers

One `spawn_subagent` call each, `agent_type=explore` (read-only: a reviewer
that edits is no longer reviewing). They run **sequentially** — this agent's
sub-agents do not run in parallel — so expect four round-trips.

Give each one the diff and one question only. A single reviewer asked all four
questions returns four shallow answers.

| Lens | The question it answers |
|---|---|
| **Reuse** | Does something in this codebase already do this? Name the file and line if so. |
| **Quality** | Redundant state, parameters that are always passed the same value, an abstraction leaking its internals? |
| **Efficiency** | Work repeated that could be done once — a query in a loop, a file read per iteration, something retained that should be released? |
| **Altitude** | Is this a patch on top of shared infrastructure that should have been fixed inside it? A guard added at one call site when every caller needs it? |

Altitude is the one that finds the expensive problems. A fix at the wrong level
looks correct and leaves every sibling caller still broken.

## 3. Sort findings before touching anything

- **SAFE** — dead code, an unused parameter, a duplicated helper. Apply it.
- **CAREFUL** — real change in behaviour at the edges. Apply only with a test
  covering the case, and say what you changed.
- **RISKY** — touches shared infrastructure or a security path. Report it, do
  not apply it. Let the user decide.

A reviewer's finding is a claim, not a fact. If one says "this duplicates
`utils.parse`", open `utils.parse` and confirm it before deleting anything.

## 4. Apply, then prove it

Run the tests after each group of fixes, not once at the end — a suite that
fails after twelve edits tells you nothing about which one broke it.

Report what was applied, what was skipped and why. "Simplified" with no diff
and no test run is not a result.
