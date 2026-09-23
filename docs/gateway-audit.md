# Gateway audit — findings and fix status

Date: 2026-09-23 · Scope: `src/agent8088/gateway/**` — runner, auth, session, agent_bridge, the Slack/Discord/Telegram/WhatsApp/Email adapters, and `whatsapp_bridge/bridge.js` — plus the engine and memory seams they call.

Method: full read of every gateway file, cross-checked against engine `check_permission` / `grant_escalation` / `run_agent` and the memory store, with runtime checks for Python-semantic suspicions. **V** = verified in code or runtime · **S** = suspected, needs a live repro.

Status (2026-09-23): H1 was fixed upstream in staging-1.2. Every other finding is **fixed** on this branch (each fix is tagged `audit <id>` in a code comment) except **L10** (bridge API token) and **L14** (global turn lock design), which remain open. Regression tests added to `tests/test_gateway_core.py`.

## High

- [x] **H1 (V) — Email From-spoof bypasses the allowlist — FIXED upstream in staging-1.2.** Rewritten at `email.py:100-137,264-267`: fail-closes when the Authentication-Results header is absent, accepts `dmarc=pass` (alignment by definition), or an SPF/DKIM pass whose `smtp.mailfrom`/`header.d` identity is domain-aligned with From (dot-anchored, DMARC-relaxed); still verified before the allowlist. Kill-switch `email_verify_sender` (default on) at `email.py:186-188`. Regression tests: `tests/test_email_sender_alignment.py` (7 tests: unaligned spoof rejected, label-boundary check, subdomain relaxation, dmarc=fail rejected). Residual hardening not implemented: still trusts the *first* Authentication-Results header — matching the MTA's authserv-id would close forged/forwarded-header cases.
- [x] **H2 (V) — Enabling Discord starves Email and Telegram.** `discord.py:128-135`, `runner.py:445-447,478-498`. `DiscordAdapter.connect` awaits `client.start(...)`, which does not return until the client closes, and `runner.run` connects adapters sequentially (slack → whatsapp → discord → email → telegram): with `discord_enabled=1` the loop never advances, so email and telegram never start and the "Gateway running" line never fires. Fix: wrap in `create_task`, as Slack already does.
- [x] **H3 (V) — `/mode` and `/plan` are process-global across all users and platforms.** `runner.py:427-428,436`. Any one allowlisted person (or a spoofed email per H1) flips `PERMISSION_MODE` to `full-auto` for the whole gateway — every other user's turns then run with no approvals; `/plan` bleeds plan-only into unrelated chats the same way. Fix: make mode per-session or at least per-chat, not a module global.

## Medium

- [x] **M1 (V) — WhatsApp session-dir default is broken.** `whatsapp.py:123-125,170-174`. `Path(config.get(...) or "")` on an empty value yields `Path(".")` (truthy — runtime-verified), so the documented `~/.local/share/...` fallback on the next line is dead code; `Popen(..., cwd=bridge_dir)` then passes that relative `--session .`, so Node resolves it against the bridge source dir and `creds.json` lands in the repo tree while Python checks the gateway's CWD. Pairing docs and reality disagree.
- [x] **M2 (V) — Bridge child `stdout=PIPE` is never drained.** `whatsapp.py:170-174,273-277`. Windows pipe buffer is ~4KB; `bridge.js` logs on every reconnect (every 3s while flaky). Fill → Node blocks on write → bridge freezes → health checks fail → restart loop. Fix: `DEVNULL` or a drain thread.
- [x] **M3 (V) — Stale Discord approval buttons can approve a different action.** `discord.py:248-256`, `runner.py:216-219`. `_lookup` keys only on `(platform, chat_id)` and views live 300s: approval A resolves early, approval B becomes pending, and clicking A's still-live ✅ resolves B while the message still displays A's tool/reason/paths. Fix: nonce bound into the view, reject on mismatch.
- [x] **M4 (V) — Every reply runs through a content-destroying dedup regex.** `runner.py:176-186`. `(.{10,80}?)(?:\1){2,}` collapses any ≥10-char substring repeated 3× — including inside code fences and tables (three identical log lines → one); the line pass also eats consecutive blank lines. Fix: run only outside fences, or drop it.
- [x] **M5 (V) — Telegram replies are markdown-converted twice.** `telegram.py:285-292` then `_send` at `:250`. `send_message` converts → chunks the converted text → `_send` converts again: `**bold**` → `*bold*` → `_bold_`. Fix: chunk raw text, convert once per send.
- [x] **M6 (V) — Gateway never passes `memory_identity`; `memory_scope_by_identity=1` is inert from the gateway.** `agent_bridge.py:62-73`, `memory/__init__.py:228-238`. Identity defaults to `owner`, so every platform user shares the CLI owner's namespace — cross-user recall and writes — and the config docstring promises per-identity scoping the bridge has no code path to supply (it never receives `user_id`). Fix: thread `user_id` through `run_turn` → `memory_identity=f"{platform}:{user_id}"`.
- [x] **M7 (V) — Dedup eviction picks arbitrary entries, not recent ones.** `slack.py:181-182`, `discord.py:152-153`, `telegram.py:204-205`. `set(list(...)[-500:])` — sets are unordered, so recent ids can be evicted under load → duplicate turns (Slack also fires `message` *and* `app_mention` for one message; only dedup stops double-processing). Fix: `dict.fromkeys` or a deque.
- [x] **M8 (V) — `/stop` doesn't stop a running turn.** `runner.py:17-18,335-340`. Help promises "Interrupt the running turn"; the handler only pops the queued list — the in-flight turn has no interrupt wired to the engine's `AgentInterrupted` and holds the global turn lock. Fix: flag the active turn for interruption.
- [x] **M9 (V) — Slack `send_message` never chunks.** `slack.py:237-240`. `MAX_MESSAGE_LENGTH=39000` exists but only the (dead, L6) stream sink uses it; a ≥40k reply is rejected and the user gets nothing but a log line. Discord/Telegram/WhatsApp all chunk.
- [x] **M10 (V) — Email adapter fails silently.** `email.py:210-219` (log at `:218`). IMAP connect/login/search errors still log at **debug** every poll — a wrong password or blocked port leaves the platform quietly dead forever. Fix: warn (with backoff) after N consecutive failures.
- [x] **M11 (V) — Malformed `From` crashes the whole poll batch.** `email.py:50-56,233-246`. A `>` positioned before the `<` (or none after it) makes `from_header.index(">", start)` raise `ValueError` in `_extract_email_address`; there is still no per-message try around `self._process_message(msg)` at `:246`, so the uid loop aborts and the offending message (possibly already `\Seen`) is lost. Fix: wrap `_process_message` per-uid.

## Low

- [x] **L1 (V)** — Plan-approval typos silently mean full-auto: anything ≠ exact `"readonly"` → `full-auto` (`runner.py:352-354`). Reject unknown args.
- [x] **L2 (V)** — `asyncio.create_task` results discarded (`runner.py:145,166`; sinks at `slack.py:78`, `discord.py:71`, `telegram.py:92`): the loop holds only weak refs — GC can drop a queued-message replay. Keep a strong ref set.
- [x] **L3 (V)** — Rate-limiter `_hits` grows forever under a `*` allowlist (`runner.py:43-55`, `auth.py:85`): any sender id in, no key eviction. Prune stale users.
- [x] **L4 (V)** — `/new` during an in-flight turn is undone: the turn's final save rewrites the just-cleared file (`runner.py:319-325` vs `agent_bridge.py:75-76`).
- [x] **L5 (V)** — Corrupt session JSON bricks the chat: unguarded `json.loads` → raw `[error: …]` on every message until `/new` (`session.py:40,49`). Treat decode errors as empty and set the bad file aside.
- [x] **L6 (V, runtime-verified)** — Subject with an embedded `name:` breaks outbound mail: a base64 encoded-word decoding to `\r\nSomething:` survives inbound parsing, then `HeaderParseError` at `as_string()` makes the reply silently return `"0"` (decoded at `email.py:274`, still assigned unsanitized at `:342`). Sanitize subject to one line.
- [x] **L7 (V)** — `bridge.js` `expandAliases` is dead code and lacks Python's `_SAFE_ID_RE` guard (`bridge.js:129-151`): never called; a JID containing `../` would reach `path.join`. Delete it.
- [x] **L8 (V)** — All four stream sinks are dead code (`base.py:51-55` + sink classes): the runner only ever calls `send_message`/`send_approval_prompt`. Wire streaming in or delete ~150 lines.
- [x] **L9 (S)** — Session-key `:` join can collide across the chat_id/thread_id boundary (`session.py:15-18`): `chat_id="a", thread="b:c"` ≡ `chat_id="a:b", thread="c"`. Practically unreachable today; encode separators if platforms grow.
- [ ] **L10 (S)** — Unauthenticated loopback bridge API (`bridge.js:317+`): Host-header check + JSON content-type cover the browser, but any local process can send/steal messages. Random token in the port file if multi-user hosts matter.
- [x] **L11 (V)** — Approval outcome race (`runner.py:243-248` vs `:369-400`): a quick `/approve` then `/deny` can show "Approved" while the engine gets `False`. Copy the verdict under a small lock.
- [x] **L12 (V)** — Email `_seen_uids` eviction is arbitrary and UIDVALIDITY is untracked (`email.py:237-240`): `next(iter(set))` isn't "oldest"; a server UID reset or >2000 unseen can re-dispatch mail after restart.
- [x] **L13 (V)** — Bridge restart can spawn a doomed second Node (`whatsapp.py:257-292`): on the "reuse existing bridge" path `_bridge_process is None`, restart still spawns a new listener → EADDRINUSE churn every ~6 polls while the original holds the port.
- [ ] **L14 (Design)** — The global turn lock is held for the full 300s approval wait (`runner.py:284-293`): other chats' agent turns stall up to 5 minutes (slash commands, including `/approve`, still respond — that part is correct).

## What held up

Platform-scoped allowlist with fail-closed strict mode (`auth.py:103-128`, tested) · percent-encoded session filenames (tested) · `_strip_special_tokens` on all inbound gateway text before it reaches the model (`agent_bridge.py:60`) · fail-closed approval timeouts on every path including Discord `on_timeout` · secrets via `get_secret`/`.env` only, never logged · markdown fences stashed before conversion in all four renderers · email verifies sender **before** the allowlist (right order — the logic is just weak, H1) · `disconnect()` paths cancel tasks and escalate terminate→kill.

## Test coverage

Gateway tests are thin: `test_gateway_core.py` (14 tests: the original 5 plus regressions for the fixes above), `test_gateway_startup.py` (no-adapter exit code), and `test_email_sender_alignment.py` (7 tests covering H1, added in staging-1.2). `test_rate_limit_status.py` covers HTTP provider headers, **not** the gateway rate limiter. Uncovered: approval flows, `/mode`//`/plan`//`/stop`, the rate limiter, dedup, session corruption, memory identity.

## Recommended fix order

Done except L10 and L14 — both are hardening/design changes left for a follow-up.
