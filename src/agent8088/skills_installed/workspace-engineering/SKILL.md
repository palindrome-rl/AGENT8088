---
name: workspace-engineering
description: Make scoped repository changes by tracing the affected flow, editing the shared cause, and running the smallest relevant check.
version: 1.0.0
category: harness
progressive: true
requires_tools: read_text,write_file
---

# Workspace engineering

1. Read the relevant implementation, its callers, and the closest focused test before changing anything.
2. Reuse the repository's existing pattern. Make the smallest patch at the shared cause, not a guard at one symptom.
3. Preserve unrelated working-tree changes.
4. Run the focused test or build check that would fail if the requested behavior regressed.
5. Report changed files and the check actually run.
