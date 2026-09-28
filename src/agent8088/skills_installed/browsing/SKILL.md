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

## Fast path

Do not launch `browse_page` for facts a targeted `web_search`, `get_page_title`,
or a direct API/MCP tool can answer. Use it only for a user-supplied page that
needs JavaScript, interaction, authenticated state, or visual confirmation.

For a browser task, give one URL, the exact success condition, and only the
necessary ordered actions. Avoid exploratory browsing, repeated page reads,
and a second browser call solely to restate a successful result. Use a separate
read only when an earlier browser action changed state and the submitted value
or confirmation must be independently verified.

This means: **if a task needs to be logged in (or otherwise mid-flow) to do
something, the login and that something must happen inside the *same*
`browse_page` call.** Never split an authenticated flow like "log in" as one
call and "now do the thing" as a second call — the second call starts over at
a logged-out state and will fail confusingly (wrong page, missing elements,
or the model incorrectly assuming it's "already logged in").

Inside that call, advance a stateful flow by clicking the site's visible links
or buttons. Do not use a direct `navigate` action to jump to a later URL after
login, adding to a cart, or filling a wizard: a full page reload can discard
client-side state even though the browser session itself is unchanged.

After an `input` action reports success, submit the form once even if the next
DOM summary does not display the field value. Retry typing only when the site
returns a validation error; otherwise repeated typing wastes the step budget
and can make a correctly filled form look broken.

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
- Navigation to the same host failing twice in a row (`ERR_CONNECTION_CLOSED`,
  DNS, timeout) — that host is dead for this session. Report the failure and
  stop; do not wander to other URLs to compensate.
- `HTTP 403` from `write_file source_url` while the page itself loads in a
  browser — hotlink protection. The site checks the Referer/UA header. Retry
  once with `browse_page`, and if the asset is only reachable from the
  page's own session, save it via the browser inside that call. Don't retry
  the direct fetch more than once — the 403 is deliberate.

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

## Saving content found while browsing

When a page has an asset (image, PDF, file) at a known URL that needs to be
saved to disk, prefer `write_file` with its `source_url` argument. `write_file`
accepts `source_url` instead of `content` to fetch and save binary/remote
content directly. Only fall back to `execute_shell` for cases where the direct
fetch can't work, such as an asset gated behind the page's own session/cookies
that only the browser can access.

Getting the right asset URL matters more than the fetch itself:

- A search result for an image usually lands on a *page*, not the image file.
  Don't browse the page to "see the picture" — extract the direct asset URL
  (from the page's `<img src>` / download link) and hand it to
  `write_file source_url`.
- Wikimedia pages: `https://commons.wikimedia.org/wiki/File:X.jpg` is a
  description page. The direct file is
  `https://commons.wikimedia.org/wiki/Special:FilePath/X.jpg` (no `File:`
  prefix needed). Same trick for Wikipedia image pages.
- Give the saved file the right extension. Use the URL's own extension when it
  has one; otherwise `write_file` derives it from the response Content-Type
  (image/jpeg -> .jpg, image/png -> .png, application/pdf -> .pdf).
