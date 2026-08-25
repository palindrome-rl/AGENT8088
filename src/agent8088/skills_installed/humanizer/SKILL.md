---
name: humanizer
description: Strip stock AI phrasing from generated prose before returning it.
version: 1.0.0
category: creative
---

Use this as a pass over prose meant to read naturally — emails, reports,
explanations to the user — before it goes out. Skip it for code, tool output,
structured data, or anything where the phrasing doesn't matter.

## What to cut

Phrases that signal "written by an AI trying to sound thorough" rather than
"written by someone who knows the answer":

- Throat-clearing openers: "I'd be happy to help with that," "Great
  question," "Let's dive into..."
- Hedge-then-answer: "It's worth noting that...", "It's important to
  understand that..." — just state the thing.
- Summary closers that restate what was just said: "In conclusion,",
  "Overall, this demonstrates...", "To summarize what we've covered..."
- Empty transitions: "That being said,", "With that in mind,", "Moving
  forward,"
- Listing everything symmetrically when the real answer has one obvious
  point and several minor ones — don't give three throwaway options equal
  weight with the one that matters.

## What not to cut

- A genuine caveat or limitation the reader needs — cutting the hedge is
  about removing empty hedging, not removing real uncertainty. "This might
  not work on Windows" is information; "It's worth noting that results may
  vary" is filler.
- Necessary structure. A numbered list is fine when the content is genuinely
  a sequence; the problem is decoration, not organization.
- Technical precision for the sake of brevity. Cutting "It's important to
  note that the function is idempotent" to "the function is idempotent" is
  the goal — cutting it to nothing loses real information.

The test: if a sentence could be deleted and nothing the reader needed would
be missing, it was filler.
