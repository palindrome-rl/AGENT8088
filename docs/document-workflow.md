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

## Images and scanned PDFs (OCR)

Attachments the active model cannot read itself are transcribed by OCR instead.
The decision is automatic and per-turn: `model_catalog.vision_capable()` answers
from the catalog's `vision` flag, and an uncatalogued pair -- every local Ollama
build and custom OpenAI-compatible endpoint -- is treated as having no vision.
Declare one in the user catalog (`vision: true`) to override.

A vision-capable model is untouched: it keeps receiving the image and OCR never
runs. For everything else, images are always OCR candidates, and a PDF becomes
one only after text extraction has already come up empty -- a text-bearing PDF
stays with `document_read`, which is faster and lossless.

Recognition is deferred to send time and cached, because the model can be
switched between attaching a file and sending it. Transcripts carry the same
untrusted-evidence framing as document text, and an OCR failure is reported to
the model rather than passed off as an empty page.

OCR needs the optional `[ocr]` extra (`rapidocr`, `onnxruntime`, `pypdfium2`).
The installers add it best-effort; without it, `ocr.available()` is false and
image attachments say so at upload time.

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

Processing keeps `document_process_concurrency` model requests in flight
(default 4, clamped to 1-16) in a rolling window, so one slow chunk does not
stall the rest. It is bounded to 48 chunks and a ten-minute pre-call deadline,
plus the normal active agent token/cost/time checks. A call already in flight
may outlast that deadline. Provider latency is not guaranteed. Chunk notes
commit in source order, so a failure keeps every chunk before it and discards
completed work after it -- the price of a resumable contiguous prefix.

Chunk size is derived from the context window minus the per-call output
reservation, converted to characters, and capped at `MAX_PROCESS_CHUNK_CHARS`
(200k). It is deliberately not derived from `max_completion_tokens`: that is
the model's ceiling for a whole chat turn, not this tool's per-call output, and
subtracting it collapsed the chunk to its 1000-character floor whenever it was
configured at or above the context window. Web-upload checkpoints are stored under the
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

## Chunking and concurrency rework (2026-09-08)

The 359.8s run above spent ~99% of its time in per-chunk model calls;
extraction of that document measures 0.25s. Two changes address that:

- Chunk sizing no longer subtracts `max_completion_tokens` and no longer
  divides tokens by 3 to get characters. On a 128k-context model the 374,711
  character report goes from 18 chunks to 2 — 9x fewer model calls, so ~9x
  less cost as well. At the shipped 32768/65000 defaults it previously
  computed 375 chunks and failed the 48-chunk preflight outright; it now
  computes 6 and runs. Regression coverage in
  `tests/test_document_access.py::test_chunk_budget_survives_completion_ceiling_at_or_above_context`.
- The fixed batch of two was replaced by a rolling window. Measured with a
  jittered stub over 24 chunks: 2.71s serial, 1.31s at 4, 0.86s at 8.

Combined, a simulation of the 87-page report at 128k context improves 8.9x
(18 calls in 9 barriered waves → 2 calls in 1). This is a wall-clock and
call-count measurement against a latency stub, not a re-run of the live
provider acceptance suite; the numbers above should be re-measured live.
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
