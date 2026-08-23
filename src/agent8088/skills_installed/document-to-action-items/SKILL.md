---
name: document-to-action-items
description: Turn a read document into a structured list of obligations, deadlines, and owners, each citing where it came from.
version: 1.0.0
category: software-development
---

Use this once `read_text` has extracted a document (contract, report, meeting
notes, policy) and the user wants to know what to actually do about it, not
just what it says.

## Output shape

A list, not a paragraph. Each item:

- **What** — the obligation or task, in one line.
- **Owner** — who's responsible, if the document names one. "unassigned" if
  it doesn't — don't invent a name to fill the field.
- **Deadline** — the actual date or trigger condition if stated ("within 30
  days of signing"), "none stated" if not.
- **Source** — the section, heading, or line it came from. This is what makes
  the list checkable against the original rather than trusted blind.

## Rules

- Every item traces to text that's actually in the document. If a "typical"
  obligation is missing (an NDA usually has a term length, but this one
  doesn't state one), say that it's absent rather than assuming a standard
  value — silently filling gaps from what documents like this "usually" say
  is how a wrong deadline gets missed.
- A document longer than one page from `read_text` (see its pagination
  header) needs every page read before the list is final — partial coverage
  read as complete is worse than saying "this covers pages 1-3 of 8, more to
  check."
- Skip items that are just descriptive/background, not asking anyone to do
  anything. The list is action items, not a document summary.
- If nothing in the document creates an obligation or deadline, say so
  plainly rather than stretching a summary into a fake action list.
