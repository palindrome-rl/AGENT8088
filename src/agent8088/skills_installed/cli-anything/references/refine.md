# Refine an Existing CLI-Anything Harness

1. Inventory every existing command, backend module, documented workflow, and
   test before editing.
2. Compare coverage with the target application's real API, executable, file
   formats, and highest-value user workflows.
3. Rank gaps by user impact, backend feasibility, and composability.
4. Preserve existing commands and output contracts unless the user explicitly
   approves a breaking change.
5. Add the smallest coherent feature group, including JSON behaviour, help
   text, state transitions, and error handling.
6. Add regression and end-to-end tests, then run the previously existing tests.
7. Update only documentation affected by the refinement.

Do not paper over backend limitations with synthetic state that the real
application cannot consume.
