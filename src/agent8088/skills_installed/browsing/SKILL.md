---
name: browsing
description: Practical guidance for driving browse_page reliably — task design, error meanings, and how to avoid corrupting form fields on multi-step tasks.
version: 1.0.0
category: workflow
progressive: true
---

`browse_page` runs a separate browsing sub-agent with its own step loop and its
own LLM calls, driven by the `task` text you give it. That sub-agent can't see
this conversation — the `task` string is its entire brief. Vague or overloaded
tasks produce vague or broken results; this skill is about writing tasks well
and reading results correctly.

## Reading errors correctly

- `Blocked: scheme '...' is not allowed` / `Blocked: '...' resolves to internal
  address ...` — the security guard fired before any browser launched. This is
  working as intended for loopback, link-local, and private-network targets.
  Don't retry with an obfuscated form of the same URL; if the target is
  genuinely supposed to be reachable, that's a config question for the user,
  not something to route around.
- `Browser error: task exceeded the Ns time limit` — the task needed more
  steps than the configured ceiling allowed. Don't just retry the same broad
  task; split it into smaller, single-purpose `browse_page` calls (e.g. one
  call to find something, a separate call to act on what you found) rather
  than one call that navigates, reads, and clicks through many pages.
- `Playwright's Chromium browser is not installed` / `browser-use package is
  not installed` — an environment issue, not a retryable failure. Say so
  plainly rather than trying the same call again.
- `get_page_title` is the cheap alternative when only the title is needed —
  reach for it before `browse_page` when interaction or full-content
  extraction isn't actually required.

## Writing the task string

- One concrete objective per call. "Fill in X, Y, Z and submit" is fine; "browse
  around and find something interesting, then do several unrelated things"
  is not — the sub-agent has no way to ask you clarifying questions mid-task.
- State field values plainly and literally (`custname = "Ada Lovelace"`), not
  as a formula or as reasoning to work out ("use the title from step 2"). If a
  value came from an earlier tool result, paste the exact string into this
  task — don't make the sub-agent re-derive or summarize it.

## Multi-step / chained tasks — verify, don't just relay

When one `browse_page` call's result feeds a later action (extract a value,
then submit a form with it), the sub-agent's own final summary is not proof
that the real page state matches it. A sub-agent can misfire mid-task —
literal stray text (including its own scratch reasoning) ending up typed into
a form field — self-detect that on one step, and then report full clean
success on the next step anyway. On a task where the actual submitted values
matter, follow the write with a read: a separate `browse_page` (or
`get_page_title`) call that visits the resulting page and reports its real
content, rather than trusting the first call's own "submitted successfully"
narration at face value.
