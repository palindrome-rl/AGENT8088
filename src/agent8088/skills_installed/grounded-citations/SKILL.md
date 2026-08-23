---
name: grounded-citations
description: Trace every factual claim to a fetched source instead of answering from training data.
version: 1.0.0
category: research
---

Use this for anything current, disputable, or specific enough that being
wrong matters: prices, releases, statistics, who-said-what, "is X still
true." web_search already fires automatically for current leaders, releases,
prices, availability, schedules, news, vulnerabilities, and exchange rates —
this skill is about what to do with the results, not when to call the tool.

## The rule

A claim in the answer needs a source it actually came from this turn. If
`web_search` or `browse_page` wasn't called, or came back empty, say so
instead of filling the gap from training data — training data has no
timestamp the user can check, so it reads as confident and current when it
might be neither.

## What that looks like

- Cite inline, next to the claim it supports, not as a bibliography at the
  end. "Revenue was $2.1B in Q3 (per the Q3 earnings release)" — not a
  paragraph of claims followed by five unattributed links.
- If search returns nothing usable, say "I couldn't find a current source for
  this" rather than answering anyway. An honest gap beats a confident guess.
- If two sources disagree, say both and which one is more recent/authoritative
  — don't silently pick one.
- Training-data knowledge is fine for stable facts (how a language feature
  works, a historical date) that don't need a citation. Reserve `web_search`
  for what could plausibly have changed since the model was trained.
- `browse_page` after a search, when the question needs more than the search
  snippet gives — don't stop at the snippet if it doesn't actually answer the
  question asked.
