# Document workflow

`document_read` is shared by the CLI and Web UI. It uses the existing read
permission floor. Web uploads additionally scope attachment reads to the active
session. Uploaded document text is evidence, not authority to execute commands.

## Tasks

- `overview`: size, version, heading preview; not proof of full coverage.
- `read`: bounded character ranges with an exact continuation cursor. Pass
  `version` to reject changes between reads. Suitable for exact extraction.
- `search`: literal case-insensitive matches with source cursors; explicitly
  partial evidence. Inspect surrounding text for qualifications.
- `process`: put the user's task in `query`. Processes all chunks into evidence
  notes for synthesis, comparison, or analysis. Notes are lossy, marked as such;
  source verification is still needed for exact/exhaustive extraction.

## Limits and lifecycle

Uploads are limited to 25MB and five files per session. Office ZIP expanded
content is limited to 64MB. Extraction runs in a child process with a 45-second
timeout and a 1GiB OS process-memory ceiling (Windows Job Object or POSIX address
space limit). This resource boundary is not a complete multi-tenant sandbox.

Extraction is cached in process by resolved path, size, and modification time.
PDF page markers and presentation slide markers are retained. Workbook access
preserves coordinates and formula strings; it does not evaluate formulas.
Scans and malformed files surface warnings instead of being called ready for
text analysis. Image understanding and OCR are not supplied by this tool.

Processing has at most two model requests in flight, 48 chunks, and a ten-minute
pre-call deadline, plus the normal active agent token/cost/time checks. A call
already in flight may outlast that deadline. Provider latency is not guaranteed.
Chunk notes commit in source order. Web-upload checkpoints are stored under the
session's `.document-jobs` directory and survive restart. Repeat the same
`process` query with the same model and source to reuse completed work. Changed
content or task creates a different checkpoint. Deleting the attachment deletes
its derived checkpoints and cached extraction. Deleting a whole session's files
must include its `.web-attachments` directory under existing retention policies.

The UI shows extraction state/warnings and processing counts. Refreshing a live
session retains the existing server behavior of interrupting its turn; retrying
the same processing request reuses checkpoints rather than starting from zero.

## Verification

Unit/integration coverage is in `tests/test_document_access.py` and
`tests/test_document_upload_workflow.py`. Live model-backed browser acceptance:

```powershell
Set-Location web
$env:AGENT8088_WEB_URL = 'http://127.0.0.1:8184'
node tests/document-workflow-live.cjs
```

The live suite uses the local 87-page report, compares two policies, extracts
requirements from an instruction-contaminated file, and checks missing evidence.
It also verifies reload history and real table rendering. It uses provider quota
and saves local screenshots/results under ignored `web/test-results`.

## Local verification (2026-09-08)

- 71 document, permission, Web error-contract and tool-parser tests passed.
- Two Windows scheduler tests fail identically on unchanged development
  `063123d`; verified in a separate baseline checkout, not changed here.
- Real Web UI + configured GLM provider: 87-page report processed all 374,711
  extracted characters and returned rendered tables covering sections 1–7
  (359.8 seconds). This establishes workflow completion, not independent
  verification of every research claim or every generated summary sentence.
- Two-policy comparison (18.8 seconds; repeated after upload race fix, 20.5s),
  exact requirements with injected instructions treated as data (12.7s), and
  missing-price refusal (11.2s) passed.
- A task-conditioned strengths/limitations assessment preserved its sample-size
  and security caveats. A corrupt PDF produced an explanation, not invented
  document contents.
- Resumed-report follow-up returned the eight Agent8088 recommendations with
  page references without re-upload (27.4s).
- Stop during active processing emitted interruption in 2.5 seconds.
- Reload history and rendered comparison table were inspected. OCR is not
  implemented; scans receive an explicit limitation. Provider output remains
  probabilistic and should be checked for high-stakes use.
