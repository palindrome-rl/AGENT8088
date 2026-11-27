# Changelog

All notable changes to Agent8088 are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- Models without native tool calling (local Ollama by default) were refused with "The conversation exceeds the
  available context" before the first request, even with a 16k context. The prompt-size check counted the tool
  list twice: once in the system prompt, where it is sent, and again as JSON schemas, which are not sent. A
  roughly 11k prompt was counted as about 20k. The send check, the fallback budget, the compaction trigger and
  the per-turn output budget now count tool schemas only when they are sent.

## [1.4.0] - 2026-11-27

v1.4 gathers the v1.3 branch maintenance work from 2026-11-20 to 2026-11-26, listed in full below. The release
channel moves to `AGENT8088-v1.4` (staging `staging-1.4`). Existing v1.3 installs stay on `AGENT8088-v1.3` when they
run `agent8088 --update`; reinstall to move to v1.4. Configuration and memory are preserved.

### Added

- A four-level verdict for every run that changed files (VERIFIED, PARTLY VERIFIED, NOT VERIFIED, COULD NOT VERIFY),
  taken from the controller's own evidence.
- Checks after every write, a checklist for multi-part requests from the first turn, play tests for interactive
  parts, auto QA, retry with change, eval mode and a clock for headless runs.
- Sub-agents end with a STATUS line and return a report to check; delegation rules and a build-test-fix procedure.
- The Tier 1 smoke set (`bench/run.py --tier 1`) and its pass bar, run before a release.

### Fixed

- Weakened, skipped or deleted given tests, and code that hard-codes a given test's case, keep work from VERIFIED.
- The WSL sandbox: Windows programs, pytest in the distro, and temporary files in the distro's own folder.
- Stale or literal verdict signals that reported finished work NOT VERIFIED.

### Changed

- Version 1.4.0. The installers, `agent8088 --update` default, CI, the sandbox image tag
  (`ghcr.io/palindrome-rl/agent8088-sandbox:1.4.0`) and the wiki sync follow `AGENT8088-v1.4`.

## v1.3 branch maintenance - 2026-11-26 (finished work reported NOT VERIFIED)

### Fixed

- Reading a log or waiting on a job (`tail`, `head`, `wc`, `sleep`, `kill`) is not a check. Before, a
  `sleep 60 && tail -1 build.log` that found no log counted as a check, because the file name has "build" in it,
  and the run ended "the latest check failed" after the build itself had passed.
- An example value in a part of the request ("amounts with two decimals, like `$12.34`") is not a name the
  delivered files must contain. The part's other named things (`subtotal`, `total`) still must be there.
- A part asking for state to outlast a reload ("still there after the page is reloaded") is met by a page that keeps
  it in `localStorage`, `sessionStorage` or IndexedDB.
- A file the run deleted itself with its own `rm`/`del`/`Remove-Item` is not reported as a missing file because the
  reply mentions it. A file the request names is still required.

## v1.3 branch maintenance - 2026-11-26 (hard-coded test cases)

### Fixed

- Work is not VERIFIED when the run added code that hard-codes a given test's case (`if celsius == 37: return 98.0`,
  where a given test asserts `to_fahrenheit(37) == 98.0`) or a comment saying the code is there to satisfy a test.
  Before, a run that did this to pass a contradictory test said VERIFIED. Python only, `==` only, and only what the
  run added: a boundary such as `if kg <= 5: return 4.0`, or a special case the project already had, does not count.

## v1.3 branch maintenance - 2026-11-26 (stale and misread verdict signals)

### Fixed

- A problem the check after a write recorded is checked again after each write and before the verdict. A page
  written before the files it points at no longer stays "points at local files that are not there" once they
  exist, which had made tested, working builds NOT VERIFIED.
- A reply's account of the bug it fixed is not read as a failed check: "your", "this" or "that" failing test,
  and lines under a heading such as "What was wrong" or "Root cause", unless the line says it still fails.
- A write the sandbox refused ("Read-only file system") is not the latest check failing.
- A `service_start` refused because the service is already running is not unresolved tool work.
- The given tests are copied on the run's first tool call, so a check weakened by a shell command, or a given test
  deleted, counts like an edit with `edit_file`.
- Bench: the Tier 1 false NOT VERIFIED rule compares the share of graded runs (`false_not_verified_per_run`).

## v1.3 branch maintenance - 2026-11-26 (temporary files in the WSL sandbox)

### Fixed

- Commands in the WSL sandbox get their temporary files in the distro's own `/tmp/agent8088-tmp`, not on the
  Windows drive. Python's `TemporaryFile` deletes a file while it is still open, which a Windows drive does not
  support, so every pytest run there crashed at teardown with `FileNotFoundError`. The runner makes the folder
  (mode 700) before the sandbox starts, and the distro's setup version goes to 3.

## v1.3 branch maintenance - 2026-11-26 (verdict fixes from the second Tier 1 run)

### Fixed

- Work that changed or skipped a check in a test the task came with is no longer VERIFIED. Before, a run changed a
  given test's expected value so every test passed, and said VERIFIED with only a test-integrity note. Adding tests,
  or changing one the request asks to change, still counts as before.
- The WSL sandbox distro has pytest. Without it, `python -m pytest` checks ended "the test tool 'pytest' is not
  installed where the check ran" over correct work. The distro's setup version goes to 2, so an existing distro is
  set up again.
- A check that failed with a setting in front of it (`BUILD_FAST=1 python build.py`) is settled when the plain
  command passes. A setting added to the rerun does not settle it.
- "The failing test" or "the previously failing one" in a reply, naming the test the task was about, is no
  longer read as a failed check, unless the reply says it still fails.

## v1.3 branch maintenance - 2026-11-26 (Tier 1 fixes under the WSL sandbox)

### Fixed

- A Windows program started from the WSL sandbox gets its bash quoting rewritten for cmd. Before, a command such as
  `cmd /c 'cd /d ... && node x.js'` reached cmd as written and failed, so a check that would have passed never ran.
- A reply that says how it reproduced the bug it fixed ("reproduce the failure") is no longer read as saying one of
  its own checks failed. Only an uppercase `FAIL` after "a" or "the" counts; a count such as "2 failures" still does.
- Bench graders:
  - `run.py` checks that its Python has the modules the graders import before the first run.
  - `pip-venv-report` runs a venv built inside the WSL sandbox in that distro, instead of failing "venv exists".
  - `tile-flip-game` no longer reads a status of "Not solved" as saying Solved.

## v1.3 branch maintenance - 2026-11-26 (Tier 1 smoke set)

### Added

- Tier 1 grows from 7 tasks and 14 runs to 12 tasks and 26 runs (about four hours):
  - `npm-esbuild-bundle`
  - three honesty tasks: `broken-test-green`, `no-change-needed` and `impossible-request`
  - one small visual task, `sales-bar-chart`
- Each new task comes with reference solutions and ones that must fail.
- The two trap tasks run three times each (`trials_by_task` in `bench/tiers.json`). `--trials` still overrides.
- The report compares with a baseline only on the tasks the baseline has, and lists the tasks added since.
- Each result records the four-level verdict (`verdict_level`), read from the whole answer. A COULD NOT VERIFY answer
  counts as not verified.
- A run on a task that cannot be done that says not verified is scored as nobody's fault.

## v1.3 branch maintenance - 2026-11-26 (Windows programs from the WSL sandbox)

### Improved

- While the WSL sandbox is active, a command that starts a Windows program (`.exe`, `.bat`, `.cmd`, `.ps1`, `cmd`,
  `powershell`, `dotnet`, `msbuild` and other Windows-only tools) runs in the Windows sandbox instead of failing in
  bash, with `/mnt/<drive>` paths turned back into Windows paths and a note that says where it ran and that the
  network is not filtered there. The model is told this in the shell's description.

## v1.3 branch maintenance - 2026-11-25 (the output cap in force)

### Improved

- The output limits named in the trace and the log are the ones a reply is really cut at. The `output_profile` trace entry
  adds `cap_in_force` and `cap_source` (the model profile, `initial_completion_cap` in config, or the model's completion
  limit), and a cap below the model profile's is logged once as a warning that says which setting set it. Before, an
  explicit `initial_completion_cap=16384` cut replies at 16,384 tokens while the announced limit said 20,000.

## v1.3 branch maintenance - 2026-11-26 (sub-agent results as reports)

### Improved

- Every sub-agent ends with one STATUS line (complete, incomplete, failed). A sub-agent the model spawned comes back
  as a labelled report: its status, a reminder that it is a claim to check against the files, its text marked as
  untrusted content, and a flag on any text addressed to the parent as an instruction. One stopped by its time limit
  reads "incomplete (stopped by its time limit; unverified)". Internal auditor and test-writer calls are unchanged.

## v1.3 branch maintenance - 2026-11-26 (a clock for headless runs)

### Improved

- A run nobody watches (`--prompt-file`, an unattended job, a task container) gets `headless_max_turn_seconds`
  (3 hours) when `max_turn_seconds` is not set, so its time checks and answer stretch work. An explicit
  `max_turn_seconds` wins, `headless_max_turn_seconds=0` turns the default off, and the REPL keeps no clock.
- When the clock stops a run before the model answered, the answer lists what is done (with its evidence), what is
  not, the files changed and the latest check, from the controller's records.

## v1.3 branch maintenance - 2026-11-26 (build-test-fix procedure and delegation rules)

### Improved

- The skill index in the system prompt ends with a short "Build, test, fix" block naming the installed skills by
  situation (runnable slice first, test-first for logic, debugging when a check fails, browser-qa for what a person
  sees or plays). The note for a check that keeps failing the same way points at the debugging skill.
- `spawn_subagent` says when to delegate (independent multi-step work only, never to review its own work), that a
  child's report is a claim to check, and not to retry a spawn refused for a limit.

## v1.3 branch maintenance - 2026-11-26 (retry with change)

### Improved

- A check that fails the same way again after the model changed files (same command, same failure line, numbers
  ignored) is called out once per repeat in the tool result and named in the task-state note until a check passes,
  so the next attempt takes a different approach. A failure that changes after the edit counts as progress.

## v1.3 branch maintenance - 2026-11-26 (auto QA)

### Added

- Auto QA: when changed work has no passing test since the change and a test suite sits above the changed files, the
  end-of-run check runs it in the sandbox (`auto_qa_seconds`, default 120, once per state of the work) instead of only
  asking the model to. A pass marks the change tested with no turn spent; a failure sends the model back with the
  command and the end of the output. `auto_qa=0` turns it off.

## v1.3 branch maintenance - 2026-11-26 (play tests and the test seam)

### Improved

- An interactive part of a request (a click, a key, a drag, a game) counts as checked only after a passing
  `browser_test` since the last change; a unit test or a page load does not show an interaction works. Without one the
  verdict stays PARTLY VERIFIED and the task-state note names the play test as the next step. Nothing is blocked or
  reopened.
- The browser-qa skill teaches a test seam for interactive builds: a read-only `window.__test.state()`, an optional
  `?seed=N` for randomness and `step(ms)` for timed games, without changing normal play.
- A part that names a file ("draw the board in game.html") is a file part even without a verb that writes it.

## v1.3 branch maintenance - 2026-11-26 (four-level verdict)

### Improved

- A run that changed files ends with one verdict in four levels, from the controller's own evidence: NOT VERIFIED (a
  check failed or the work is unfinished, now also when a written file still does not parse), COULD NOT VERIFY (an
  environment limit stopped the check and nothing passed), VERIFIED (a test passed after the last change and no part
  waits for a recheck) and PARTLY VERIFIED (anything else, with what is missing). NOT VERIFIED stays above the reply;
  the others lead the end-of-run notes as a `Verdict:` line. What the reply claims never raises the level.
- The auditor must cite its evidence for a `fail` (the command and the output line, or the file and line), and is
  told a `fail` the caller cannot reproduce is thrown away.

## v1.3 branch maintenance - 2026-11-26 (checks after every write)

### Added

- A file landed by `write_file` or `edit_file` is checked at once by a parse that runs nothing: Python and JSON are
  parsed, JavaScript goes through `node --check`, and an HTML page's local `src`/`href` files must exist. A problem
  is added to the tool result in one line and kept in the task-state note until the file is written cleanly, so a
  broken file is found on the next turn instead of at the end of the run. Appends and files over 1 MB are not checked.

## v1.3 branch maintenance - 2026-11-26 (checklist and ledger)

### Improved

- The parts of a multi-part request are on the checklist from the first turn, not only when the final answer is
  checked. Each part records its kind (file, run, test, interactive or property), what settled it, and the state of
  the work at that moment. A run, test or interactive part settled before a later change goes back to "recheck" until
  a check passes again. The task-state note lists each part with its status and evidence and names the next step; it
  is rebuilt from the controller's state, so it survives compaction.

## v1.3 branch maintenance - 2026-11-26 (eval mode)

### Added

- Eval mode, for runs judged on what they leave on disk (measured experiments, comparisons): `run_mode=eval` in
  config, `--eval`, `AGENT8088_RUN_MODE=eval`, or `/eval on` in the REPL. It leaves out the last-stretch ask to stop
  and write the answer, the optional look at a page, and the last-state screenshot of a failing `browser_test`;
  every check whose result can lead to a fix stays. `full` is the default and changes nothing. Each run's trace starts
  with a `run_mode` entry, and `/status` shows the mode.

## v1.3 branch maintenance - 2026-11-25 (looking at what was built)

### Added

- `screenshot_page` adds a short "Layout notes" line for a page when something is off: the page is larger than the window,
  text that is hard to read against its background, or text under 10 px. Nothing is said when nothing is off.
- When a run built a page or image and the request is about how it looks, the final check asks once to render it and look,
  if nothing in the run has (`completion_visual_look`, on by default). It is last in the order, never blocks, and never marks
  a run as not completed.

## v1.3 branch maintenance - 2026-11-25

### Added

- `task_state_delivery=history` (default `system`, unchanged) sends the harness's task state to the model once, when it
  changes, at the end of the newest tool result, instead of rewriting the end of the system prompt on every turn. The
  system prompt and all earlier messages then stay exactly as the server saw them, so a server that caches prompts can
  reuse the whole previous request. `task_state_refresh_turns` and `task_state_max_notes` bound how often it repeats.

## v1.3 branch maintenance - 2026-11-25 (post-check window)

### Improved

- A run is no longer ended while it is still gathering new evidence after a passing check. Reading a file or range for the
  first time, or taking a new screenshot or page check, is held and not counted against `max_post_check_rounds`. The budget is
  bounded by `post_check_ceiling_turns` (default 15 quiet turns), nothing is held once the wrap-up nudge was sent, a write
  starts over, and `post_check_new_evidence=0` restores the old count.

## v1.3 branch maintenance - 2026-11-20

### Added

- `screenshot_page` renders a local page or image-type file (`.html`, `.htm`,
  `.svg`, `.png`, `.jpg`, `.gif`, `.webp`, `.bmp`) in headless Chromium through
  the same guarded loader as `check_app` and attaches the picture to the next
  model message. Its evidence is deterministic: well-formedness for XML-based
  files, page and console errors, blocked requests, a blank-render warning, the
  change from the previous render, and whether the page is still moving.
- `view_image` is a core tool: the agent can look at any local image in the
  workspace (URLs only with `image_tool_allow_urls`).
- The `browser-qa` skill: verify visual and interactive work by loading it,
  exercising its main action with a headless test script, rendering it, and
  fixing what is wrong with targeted edits.
- Image input is decided from a `provider.<name>.vision` setting, the model
  catalog, what the endpoint publishes, or one cached live probe with a
  known-answer picture. `/vision [probe]` shows and re-checks the decision;
  `/status` and `/capabilities` report it.
- `scripts/check_generic_terms.py` checks that a branch's added lines and commit
  messages stay generic.

### Improved

- The model gets a downscaled copy of tool-attached images
  (`model_image_max_side`, `model_image_max_bytes`), only the most recent images
  stay in the conversation (`model_image_keep`), a server's per-request image
  limit is learned from its refusal, and a text-only model gets the evidence text
  instead of image parts.
- README and wiki counts: 61 built-in tools and 40 skill packages.

### Fixed

- The fallback-model output budget no longer counts an image's base64 data.
- A failed model call after an image tool no longer prints the image data.

## v1.3 branch maintenance - 2026-10-18

### Fixed

- A failed script check settles after a clean retry without mistaking a plain
  script startup for a passing test suite or accepting a printed traceback.
- End-of-run verification notes survive output cut-offs and repeated-call stops.
  Closing caps preserve fresh test evidence without hiding missing deliverables.
- Page checks exercise dropdown choices, identify page-loaded scripts by their
  actual local paths, and distinguish missing-backend limitations from code faults.
- A model-invoked page-check failure remains visible until that same target
  passes; another page with the same filename cannot clear it.
- Required-output detection splits at whole-word conjunctions, and generated
  project folders stay together across writes, including after folder deletion.
- Strict camelCase, snake_case and lockfile integrity values avoid unnecessary
  credential warnings while credential-prefix checks remain enforced.
- Optional mem0 absence falls back quietly, while broken installed packages and
  unexpected initialization failures remain visible.
- Verification recognizes redirected and chained test commands, reports its
  assurance level, and does not reuse a smoke-test pass after a later edit.
- Project test discovery chooses an appropriate Python or npm runner instead of
  running package modules as standalone scripts or inventing an npm test script.
- Large diagnostic banners are bounded so a single tool result cannot exhaust
  the model context; full output remains available through content handles.
- Credential-shaped blobs remain flagged while code identifiers and model sizes
  avoid unnecessary warnings.
- Page checks distinguish missing-backend response decoding from unrelated
  JavaScript failures. Ambiguous status-text errors remain unverified.
- Plan-mode web searches can request one-time or session approval without being
  treated as attempts to modify files; approved searches remain read-only.
- Required-output checks recognize files in the requested project folder without
  accepting unrelated input directories or relocating explicit root-level outputs.
- Static page checks distinguish unavailable backend routes from missing assets,
  including files whose names begin with `api`.
- Installed launchers find the appropriate user or project configuration, including
  activated project environments; configuration reads and writes use the same precedence.
- Missing local Ollama embeddings fall back quietly to keyword search. Remote,
  custom and proxied embeddings retain their configured client transport.

### Improved

- New default allowances are 150 starting rounds, a progress-based ceiling of
  450, and a 1200-second tool-timeout ceiling. Existing configured values and
  time/token/cost budgets are preserved. `/limits` is listed at startup.
- Windows native sandbox checks report verified piped-child-process limitations
  and offer Docker guidance without changing sandbox permissions or isolation.

Public installation links, update channel, version, license and existing
public-specific fixes are unchanged.

## v1.3 branch maintenance - 2026-10-16

### Improved

- If context compaction receives an empty summary, it retries once with reduced
  reasoning and a fenced transcript. An empty retry or a tool-call-shaped reply
  leaves the original conversation intact.
- When a run is stopped by its closing cap, the page or script is smoke-checked
  once, and the answer starts with a NOT VERIFIED line when the run saw a clear
  sign of trouble.
- A run that repeats itself is warned in steps before it is ended.
- Productive research may run more searches; a refused search ends with guidance
  and the files gathered so far.
- A relative `artifacts/` path is kept as written after a command changes
  directory.
- Credential scanning no longer flags plain words, MIME type lists, encoded URLs
  or source-code tokens.

Public installation commands, update channel, version 1.3.0, MIT licensing and
existing public-specific diagnostics and Web UI fixes remain preserved.

## [1.3.0] - 2026-10-15

The public channel moves to the `AGENT8088-v1.3` branch. Installers,
`agent8088 --update`, the Docker image and the wiki follow it. Existing
configuration and memory are preserved.

### Added

- `copy_file` and `move_file` copy an existing local file byte-for-byte (PDFs,
  images), for example to file an attachment into `Drive/Finance/`. A folder
  destination keeps the file's name. A missing folder is created only when you
  named it; otherwise the file stays in `artifacts/` and the reply says so.
  `move_file` only moves files from inside the project.
- Credential-shaped strings in pasted input and written files are detected, and
  the web chat warns before sending one. Product and version tokens are no
  longer reported as credentials.
- The model is told when its time or rounds are running out.

### Improved

- Completion checks compare each part of a request with what the run delivered,
  ask once for a lookup when a build needs facts the run never checked, and send
  a reply back while the latest automated check is still failing. Only a command
  that runs a check can mark a run as failing one.
- Page smoke tests keep clicking after the first control and judge the page as a
  whole. Script smoke tests tell "could not start here" from a crash, keep a
  failed start's output, and recognise failures that have since been fixed.
- A run that brings nothing new is asked to decide; a finished run is told to
  finish. Empty replies and unknown-tool calls count toward the in-a-row limits.
- Your rules are kept word for word through context compaction.
- A write diverted to `artifacts/` is checked where it landed, and is copied to
  the path you named. A supplied input file is no longer demanded as output.
- Native Windows shell commands start in the workspace.
- The plan file, web trace export, headless resume and sub-agent routing are
  more reliable.

### Security

- `copy_file` and `move_file` refuse another session's web attachment, as
  `read_text` already did.

### Fixed

- A plan file that cannot be written is logged instead of raising an error.

## v1.2 branch maintenance - 2026-10-14

### Improved

- Reasoning-only cutoffs temporarily disable thinking for a configurable number
  of following calls. Truncated tool arguments receive smaller-chunk guidance.
- Safe partial text writes can be recovered through normal permission and backup
  guards, with unfinished files tracked by full path until continued.
- Configured credentials are blocked in file content, append boundaries, final
  edited text and downloaded UTF-8 content; denial messages do not reveal values.
- Smoke checks skip scratch helpers, use the correct native Windows directory,
  and report browser-only JavaScript startup as unverified rather than passed.
- Verification uses recorded evidence and confirms failed audits before rollback;
  inconclusive results preserve changes and clearly report what remains unchecked.
- Browser smoke checks stop only their own browser processes and distinguish
  blocked external dependencies from real application errors.
- The local smoke-test server reliably rejects write requests on Windows,
  draining only a size- and time-limited request body before returning HTTP 405.
- Idle stream recovery works with OpenAI and LiteLLM; empty and reasoning-only
  cutoffs receive bounded recovery, with unfinished work reported at run limits.
- Conversation compaction preserves earlier summaries and carries forward verified
  checks and changed-file records instead of repeatedly rewriting the same facts.
- Existing files receive recoverable, size-limited backups before their first
  overwrite in a session, with configurable retention and storage limits.
- Nullable tool arguments and MCP schemas are validated consistently; semantic
  tool retrieval and native tool instructions remain configurable.

Public installation commands, update channel, version 1.2.0, MIT licensing and
existing public-specific diagnostics and Web UI fixes remain preserved.

## v1.2 branch maintenance - 2026-10-13

### Improved

- Shared completion checks catch unfinished work, missing outputs and unchecked
  changes with a bounded follow-up budget. `check_app` smoke-tests generated
  pages and scripts, with configurable reporting or enforcement.
- Adaptive output limits, thinking-off recovery and chunked file writes keep
  long replies and repetitive rewrites bounded. Successful tests no longer
  trigger redundant audits.
- Provider errors inside streams are retried; keyword tool ranking still works
  without embeddings, and MCP text results preserve structured data when present.
- Durable tasks can be deleted safely after their workers stop. Chat switching,
  queued commands and automatically named sessions retain the correct identity.
- Document extraction caching, persistent memory recall, Windows ESC handling
  and CLI reasoning scrollback are more reliable.
- Docker allowlist networking uses an egress proxy. Windows native sandbox
  documentation accurately describes write-only confinement and non-admin setup.
- The public Doctor page allows a bounded two minutes for live health probes;
  desktop and mobile acceptance checks wait for completed diagnostic results.

Public installation URLs, update channel, version 1.2.0, MIT licensing and
public-specific diagnostics remain preserved.

## v1.2 branch maintenance - 2026-10-12

### Improved

- Interactive CLI conversations automatically get a saved name after the
  first message, with source/model metadata and restart/resume support.
- The Web UI can start or resume another chat while a task runs. Transcripts,
  answers and approvals stay with their owning chat; queued messages retain
  their destination. Running chats are protected from rename, delete and reset.
- Session writes share the atomic, private file writer. Plan completion and
  queued-turn streaming state retain the correct session identity.
- Windows sandbox setup and execution consistently choose a supported Node.js
  runtime, preferring the managed installation. Setup can download checksummed
  Node.js 22.11.0 when needed without replacing an existing installation with an
  incomplete download.

Public installation commands, update channel, version 1.2.0, MIT license and
existing public-specific diagnostics and UI fixes are preserved.

## v1.2 branch maintenance - 2026-10-09

### Fixed

- Requests now start with 50 rounds consistently across the CLI, Web UI,
  gateways and direct engine calls; productive runs retain bounded growth.
- Resuming older sessions without a stored turn limit preserves the current
  setting instead of silently resetting it to 10.
- Time, token and cost budget messages identify the limit actually reached.
- Controller-generated output-location notes identify sandbox workspace output
  when it differs from the project folder.
- The Web UI `+` menu starts search and memory requests rather than backend
  configuration commands. Commands with ready attachments are refused without
  discarding the composer or files, including while another turn is running.

Public repository URLs, installation scripts, update channel, version 1.2.0,
MIT license and existing public-specific UI fixes are unchanged.

## v1.2 branch maintenance - 2026-10-07

### Improved

- Shared capability health reporting across the CLI, Web UI, gateway and Doctor.
- Search fallback/recovery notices, transient DDGS retries and bounded dynamic
  search allowances while queries continue finding new evidence.
- Working-directory recovery and failure diagnostics, output-limit recovery,
  verification accounting and bounded post-check loops.
- CLI readability, toggle status commands, timestamped diagnostic exports,
  memory capture, provider discovery and local-model context reporting.
- Scanned-document OCR, gateway dependency reporting and Docker browser access.

### Fixed

- Windows OpenCodeReview version pin scope under `iex`, missing download
  diagnostics, and false readiness based only on an executable's presence.
- OpenCodeReview now retries transient network failures with bounded backoff;
  both installers verify the pinned executable before reporting success.

Public version metadata remains 1.2.0. Public installation URLs, MIT licensing,
CI, support files and existing installation-recovery safeguards are preserved.

## [1.2.0] - 2026-09-29

### Added

- Small-model-first execution: efficient prompts, minimized tool schemas and
  model-aware routing. `/local check` probes your hardware and
  `/local available` lists local Ollama models that fit it.
- Verification-gated completion: tasks are checked against their required
  outputs, tests and observable tool results before completion is reported.
- Robust context management: pre-call budgeting, overflow prevention,
  compaction, content handles and persistent task state.
- A web UI alongside the CLI, with live diffs.
- Local OCR for reading screenshots and scanned PDFs without a vision model.
- One-command installers for macOS, Linux, WSL2 and Windows.
- A Docker image, published to `ghcr.io/palindrome-rl/agent8088`.
- MCP client and server, skills, focused sub-agents, and Slack, Discord,
  WhatsApp, Telegram and email gateways that share one agent loop and
  permission layer.
- An optional audit log (`audit_log=1`).

### Security

- `readonly`, `plan-only` and `full-auto` permission modes, a native OS sandbox
  for shell commands, credential protection, and SSRF and egress controls.

## [1.0.0] - 2026-08-18

### Added

- A local-first, tool-using agent runtime with one session and permission layer
  shared by the CLI and the messaging front ends.
- A three-tier permission engine (`readonly`, `plan-only`, `full-auto`) with
  per-action approval, credential-path protection, and SSRF and egress controls
  at the tool-call boundary.
- Built-in tools for the filesystem, shell, Git, Chromium browsing, web search
  (SearXNG, Tavily, Exa, DDGS) and scheduling, also served over MCP stdio
  (`--mcp-serve`), plus connections to external MCP servers.
- A native OS sandbox for shell commands, with a Docker fallback.
- Restricted sub-agent profiles for context-isolated work.
- Hybrid keyword and semantic memory over a local SQLite store.
- 12 provider profiles, custom OpenAI-compatible endpoints and local Ollama,
  with fallback chains on retryable failures.

[1.3.0]: https://github.com/palindrome-rl/AGENT8088/releases/tag/v1.3.0
[1.2.0]: https://github.com/palindrome-rl/AGENT8088/releases/tag/v1.2.0
[1.0.0]: https://github.com/palindrome-rl/AGENT8088/releases/tag/v1.0.0
