---
name: browsing
description: Practical guidance for driving browse_page reliably — task design, session/login state, error meanings, and verifying results on multi-step tasks.
version: 1.1.0
category: workflow
progressive: true
---

`browse_page` launches a separate browsing sub-agent with its own step loop
and its own LLM calls, driven entirely by the `task` string you give it. That
sub-agent cannot see this conversation and has no memory of any earlier
`browse_page` call — the `task` string is its entire brief, every time.

## The rule that matters most: no session carries over between calls

**Every `browse_page` call starts a brand-new, logged-out, cookie-free
browser.** Nothing persists between calls — not a login, not a cart, not
anything clicked or typed in a previous call.

This means: **if a task needs to be logged in (or otherwise mid-flow) to do
something, the login and that something must happen inside the *same*
`browse_page` call.** Never split an authenticated flow like "log in" as one
call and "now do the thing" as a second call — the second call starts over at
a logged-out state and will fail confusingly (wrong page, missing elements,
or the model incorrectly assuming it's "already logged in").

This is the opposite of the general task-sizing advice below, and it wins
when the two conflict: a login-gated checkout, a multi-page wizard, or
anything else that depends on state set up earlier belongs in **one** call
with the whole sequence spelled out, not several smaller ones.

Splitting into multiple calls is still the right move when each call is
genuinely independent — e.g. extract data from one page, then act on a
*different*, unrelated site with what you found (see "Chained tasks" below).
The test is: does step 2 depend on browser state step 1 created? If yes, one
call. If no, split freely.

## Writing the task string

- Be concrete and literal. State field values plainly (`custname = "Ada
  Lovelace"`), not as a formula the sub-agent has to work out. If a value
  came from an earlier tool result, paste the exact string in — don't make it
  re-derive or summarize.
- For a flow that must stay in one call (see above), write it as an ordered
  list of concrete steps, not a vague goal — "log in, click X, fill Y, click
  Z, report the confirmation text" beats "complete an order." Each call has a
  finite step/time budget shared across the whole flow; wasted exploration
  steps figuring out a vague instruction can exhaust it before the flow
  finishes.
- If a legitimately complex single-call flow still hits its step or time
  limit (`Browser error: task exceeded the Ns time limit`), that's a budget
  problem, not something to fix by retrying the same call, and definitely not
  by splitting it into pieces that will lose the session. Report the limit
  back rather than silently retrying.

## Reading errors correctly

- `Blocked: scheme '...' is not allowed` / `Blocked: '...' resolves to
  internal address ...` — the security guard fired before any browser
  launched. Working as intended for loopback, link-local, and private-network
  targets. Don't retry with an obfuscated form of the same URL.
- `Browser error: task exceeded the Ns time limit` — see above: a budget
  problem for that specific call, not a transient failure.
- `Playwright's Chromium browser is not installed` / `browser-use package is
  not installed` — an environment issue. Say so plainly; don't retry.
- `get_page_title` is a plain HTTP fetch (no browser, no JS, no login) —
  reach for it before `browse_page` when only a title is needed and no
  interaction or authenticated content is involved.

## Chained tasks — verify, don't just relay

When one `browse_page` call's result feeds a *later, independent* action
(extract a value, then use it elsewhere), the sub-agent's own final summary
is not proof the real page state matches it. A sub-agent can misfire mid-task
— its own scratch reasoning ending up typed into a form field, for
example — self-detect that on one step, and then report full clean success
on the very next step anyway. When the actual submitted/extracted values
matter, follow up with a separate read (another `browse_page` or
`get_page_title` call that visits the resulting page and reports its real
content) rather than trusting the first call's own narration at face value.
