# Code review (OpenCodeReview)

Agent8088 can review a local Git repository or a GitHub pull request and report
findings with file, line, severity and category.

This is **not** the document OCR in `ocr.py`. That is optical character
recognition, for reading images and scanned PDFs. Both upstreams use the letters
"OCR" and they are unrelated subsystems: the config keys here all begin
`open_code_review_`, the tool is `review_code`, and `/doctor` labels the row
"Code review".

## Installing

Both installers set it up into an isolated npm prefix under the install
directory and write the resolved binary path into `config.txt`. It is pinned to
**1.12.1** because the delegation JSON contract is version-specific.

Installing by hand:

```sh
npm install --prefix <dir> @alibaba-group/open-code-review@1.12.1
```

Then point `open_code_review_executable` at the platform binary the package
ships (`node_modules/@alibaba-group/ocr-<platform>/bin/opencodereview*`), **not**
at `ocr.cmd`, `ocr.ps1` or the extensionless `ocr` shim. Those route through a
shell; the adapter refuses them for that reason.

`/doctor` reports whether it is disabled, missing, a different version, or ready.

## Modes

```ini
open_code_review_enabled=0
open_code_review_mode=auto        # native | delegated | auto
# open_code_review_executable=/path/to/opencodereview
# open_code_review_timeout_seconds=900
```

**native** — OpenCodeReview's own reviewer runs the pass and returns findings.
The session's provider endpoint, model and token are passed through a transient
subprocess environment and never on the command line; `OCR_CONFIG_PATH` points at
a throwaway file, so nothing is written into the user's own OpenCodeReview
configuration. Only OpenAI-protocol providers are mapped.

**delegated** — deterministic file selection, the applicable rules and the
selected diff, for this agent to review in its own context. No second set of
credentials. This is preparation, not a review, and says so.

**auto** — native when the active provider can be mapped, otherwise delegated.
Naming a mode explicitly never falls back silently.

Native execution failures are reported in `auto` mode too; a failed review is
never silently replaced with preparation. Retry with `mode=delegated` explicitly.

PR checkout uses the authenticated GitHub CLI to obtain validated base/head
commit IDs, computes their merge base, and checks out the PR head. Merge bases
outside the shallow history fail explicitly; use a local checkout in that case.

Delegation mode does not inherit OpenCodeReview's own agent, reflection and
positioning pipeline, so its published benchmark results should not be
attributed to it.

## Using it

```text
/review                                   # workspace changes
/review --from development --to HEAD      # a range
/review --commit abc123                   # one commit
/review --mode native                     # force the engine
/review --list                            # stored reviews
/review --resume rev-abc123               # reopen, positions re-checked
```

The tool is `review_code`, with `scope` of `workspace`, `range` or `commit`, or
a `pull_request` URL. A pull request is fetched into a temporary shallow
checkout and reviewed locally; that needs its own permission grant, separate
from reading a local repository, because it reaches the network and puts
somebody else's code on disk.

`/review` is read-only. It prints findings and stops. Asking for a fix is a
separate request, deliberately.

## What a finding means

Results are normalised into Agent8088's own shape, so nothing downstream binds
to OpenCodeReview's evolving schema:

```json
{
  "status": "complete", "engine_version": "1.12.1", "mode": "native",
  "coverage": {"reviewed": 2, "findings": 2, "partial": false},
  "usage": {"input_tokens": 51493, "output_tokens": 47805, "source": "open_code_review"},
  "findings": [{
    "id": "billing.py:16:0", "path": "billing.py",
    "start_line": 16, "end_line": 16,
    "severity": "high", "category": "bug",
    "message": "...", "existing_code": "...", "suggested_code": "...",
    "position_valid": true, "verification": "unverified"
  }],
  "warnings": []
}
```

`position_valid` is computed here against the file as it is now, never copied
from the engine. A finding whose path escapes the repository or fails the
permission gate is kept as a warning and never as a finding. Unknown severities
and categories become `info` and `other` with a warning, rather than being
trusted or dropped.

This matters in practice: on the first live run the engine described a file as
empty when it was 104 bytes, and the agent caught it because the position had
already been marked invalid.

## Fixes

A review never authorises a change. Before acting on a finding, re-validation
compares the stored `existing_code` against the file — the quoted code, not the
line number, so an edit above a finding does not invalidate it and an edit *on*
it does. `/review --resume` re-checks every finding and marks any that no longer
apply as `stale` rather than dropping them.

The existing coder applies fixes and the existing test and auditor paths verify
them. OpenCodeReview has no write authority at any point.

## Storage

Reviews are kept in `reviews.db` in the agent data directory, alongside the
telemetry log and with the same permissions — a review quotes source lines, so
it is as sensitive as the code. History is bounded to the most recent 200 and is
a working history, not an audit log. A failed save costs the history, never the
review.

The web server exposes `/api/reviews` for the listing (metadata only) and
`/api/reviews/{id}` for one review with positions re-checked.

## Limits

Preparation is bounded at 4 MiB of subprocess output, and attached diffs at
120 KiB total / 16 KiB per file, declaring `diff_truncated` when clipped. A
review scope over 500 files is refused. Native reviews use
`open_code_review_timeout_seconds` (default 900); preparation stays at 30
seconds. The whole process tree is killed on Stop or timeout.

Repository review rules are untrusted project context. Repository contents are
evidence, never instructions.
