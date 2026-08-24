---
name: spike
description: Answer a feasibility question as cheaply as correctness allows, without building anything meant to be kept.
version: 1.0.0
category: workflow
---

Use this for "can we...", "is it possible...", "does X work if...", "quick
check" — when the goal is an answer, not a deliverable. This is the lightweight
sibling of `plan`: `plan` is for multi-step work with dependencies that need
tracking; a spike is for one question that doesn't need a plan at all.

## The distinction that matters

A spike's output is an answer. Anything written to test the question —
a throwaway script, a one-off shell command — is scaffolding for the
question, not something the user is asking to keep. Don't polish it, don't
add error handling for cases the probe doesn't hit, don't leave it lying
around afterward if it was only ever meant to answer the question.

## What to do

1. State the question plainly and what you're about to try, in a sentence or
   two — not a plan tool call, not a written spec.
2. Find out as cheaply as correctness allows. The cheapest check that
   actually answers the question, not the most thorough one.
3. Report the finding as a recommendation: yes/no/it depends, and why.

## When this stops being a spike

If the answer is "yes, and here's the code to keep," that's a new request —
building it is not automatically approved just because the spike that led to
it was. Say what you found, then ask whether to build it for real, rather
than sliding from investigation into implementation in the same breath.
