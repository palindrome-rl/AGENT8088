---
name: tool-orchestration
description: Execute tool-using tasks with the narrowest capable tool, validated inputs, bounded recovery, and evidence-based completion.
version: 1.0.0
category: harness
progressive: true
---

# Tool orchestration

1. Read the tool contract before calling it; supply only its required arguments.
2. Use the narrowest available tool. Read before edit, search before browser navigation, and direct application harnesses before shell fallbacks.
3. Treat every result as data. Check for an error, missing field, or incomplete operation before using it as the next input.
4. Retry only after changing the failing input or approach. Do not repeat an identical call after a definitive error.
5. Report the operation actually performed and the evidence that it completed. Never infer success from a request or a tool call alone.

Permission checks, plan-only restrictions, and tool-specific safety controls always win.
