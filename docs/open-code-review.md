# OpenCodeReview integration development

This branch adds an optional `review_code` tool. It calls OpenCodeReview
1.12.1's delegation preview and rule commands. Agent8088 then uses its existing
model and file tools to review the selected source. Preparation alone is not a
completed review, and the response explicitly reports zero reviewed files.

## Configuration

Set `open_code_review_enabled=1` and `open_code_review_executable` to the absolute
path of the native OpenCodeReview executable. Shell launchers are not accepted.
The exact executable version is checked on each preparation call. The tool is
disabled by default and does not install software or configure credentials.

The existing `[ocr]` Python extra is optical character recognition for documents;
it does not provide OpenCodeReview.

## Supported scopes

- `scope=workspace`: current local workspace changes.
- `scope=range`, `base=development`, `head=HEAD`: branch comparison.
- `scope=commit`, `commit=<revision>`: one commit.

Use `repo` for the local repository directory. Review preparation follows the
normal host-process permission gate and rejects plan mode. Protected source paths
are rejected. Output is bounded to 4 MiB per subprocess and scopes to 500 files.
Each subprocess checks cancellation and the active turn budget. Large results use
the existing `last_output` reference mechanism.

## Verification

`scripts/live_code_review.py --executable <native-binary>` creates a disposable
Git repository, runs the real Agent8088 loop using configured Ollama Cloud GLM 5.3,
and checks that preparation and source reading occur and the planted percentage
bug is identified. Credentials come from the existing configuration.

## Remaining implementation

The full proposed feature is still under development: native review execution,
managed installation, durable finding storage, enforced coverage and fix handoff,
and dedicated CLI/Web UI presentation are not implemented by this preparation tool.
