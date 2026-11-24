# Implementation Plan

## Milestone 0 — Repository foundation
- [x] Create repository baseline and trust-boundary documentation.
- [x] Define configuration and secret handling.
- [x] Define fail-closed public/owner access defaults.
- [x] Add minimal Telegram long-polling transport.
- [x] Add tests and CI.
- [x] Verify CI on GitHub Actions (Python 3.11 and 3.12).

## Milestone 1 — Production-grade Telegram core
- [x] Add stable local installation identity.
- [x] Add owner-only pairing identity command.
- [x] Add owner command routing for the initial Hub client.
- [x] Keep Hub integration disabled unless explicitly opted in.
- [x] Redact bot credentials from Settings representations and provider exceptions.
- [x] Validate Telegram update envelopes and bounded message variants with typed structural parsing.
- [x] Persist the polling cursor atomically and advance only after updates are safely ignored or successfully handled; Hub task submissions use update-scoped idempotency keys.
- [x] Add structured metrics without message content.
- [ ] Add webhook receiver after deployment threat review.

## Milestone 2 — Identity and owner pairing
- [x] Implement Hub-side bot client registration and revocable scoped credentials (Hub main).
- [x] Add one-time owner pairing challenge and Telegram identity binding (Hub main).
- [x] Add capability scopes, client listing and revocation (Hub main).
- [x] Add EL_AI Hub client with OS credential-vault storage (EL_AI main).
- [x] Add explicit Hub integration opt-in and HTTPS remote URL guard (EL_AI main).
- [x] Add tests for pairing, expiry, scope enforcement, refresh and revocation.
- [x] Add owner-facing Hub UI for challenge creation and client revocation (Hub main).
- [x] Scope Telegram task listings to the exact paired client (Hub main).
- [ ] Complete a live local pairing test after owner-managed runtime secrets are configured.

## Milestone 3 — Public assistant
- [x] Integrate only the public MR_SIAVASH_AI Gateway contract (feature PR under CI).
- [x] Label grounded AI responses separately from knowledge fallback.
- [x] Add bounded message/response sizes, process-wide quota and source-link validation.
- [x] Keep public access disabled by default and never forward private Hub context.
- [ ] Verify a live production Gateway response from the configured Telegram bot runtime.
- [ ] Add a broader public help and knowledge evaluation set.

## Milestone 4 — Memory and planning
- [x] Add owner-scoped memory search through the Hub API contract.
- [x] Add bounded task submission through Hub PolicyEngine and idempotency.
- [x] Add owner-authored bounded task-plan previews, explicit resumable idempotent batch submission, aggregate plan status, and structured task-watch progress notifications.
- [x] Add bounded AI-generated sequential task-plan preview through Hub; local provider is default, remote Kimi requires explicit selection, and preview never creates or executes tasks.
- [x] Add bounded dependency-aware task-plan DAGs, explicit root-first plan start, success-gated progression, persisted blocked checkpoints, idempotent recovery, and structured progress notifications.
- [x] Add a read-only E8 evidence snapshot for owner-scoped task plans: successful task statuses count as evidence only when the Hub result envelope is structurally valid; missing/unknown evidence is surfaced without retries or mutation. Semantic acceptance-criteria evaluation and automatic correction remain incomplete.
- [x] Add runtime-level owner/public message-routing isolation coverage: owner private text never reaches the public Gateway, and public messages send only their own text (PR #67).
- [x] Extend isolation coverage to prove private Hub results and task context never cross into the public Gateway or public Telegram replies (PR #67).

## Milestone 5 — Bounded Agent capabilities
- [ ] Expose named Agent capabilities only after a separate scope and policy review.
- [ ] Add idempotency, timeouts, budgets and audit correlation.
- [ ] Require Hub approval for consequential side effects.

## Milestone 6 — Release
- [ ] End-to-end Telegram tests using a real test bot and owner-approved runtime secrets.
- [ ] Security review and dependency audit.
- [ ] Deployment runbook, backup/restore and incident response.
- [ ] Production release and live verification.

## Current truthful status

The Telegram foundation, Hub pairing client, explicit opt-in and secret-redaction hardening are merged and tested. Hub pairing APIs, owner UI, and per-client task isolation are merged and tested. The public website assistant client is integrated on `main` and CI-tested on Python 3.11 and 3.12. Live Telegram runtime acceptance remains unverified. Public conversations and Hub access remain disabled by default. No bot token has been configured, no live Telegram bot has been connected, and no production bot deployment has occurred.


## Evidence refresh — 2026-11-20 UTC

- Repository `main` at review: `2cca575fac240eb7c86c924b9b51f934ff57608f`.
- Exact-main CI checks for Python 3.11 and 3.12 completed successfully; workflow-security audit and supply-chain posture checks also completed successfully.
- Typed update parsing is implemented in `src/el_ai/telegram/updates.py` and covered by `tests/test_updates.py`.
- The crash-safe cursor is implemented in `src/el_ai/telegram/cursor.py`; `src/el_ai/main.py` advances and persists offsets only after safe ignore or successful reply delivery. `tests/test_cursor.py` and `tests/test_update_offset.py` cover persistence and monotonic offset behavior.
- Task creation uses an update-scoped idempotency key at the Hub boundary. This does not claim exactly-once delivery for every Telegram reply: an ambiguous send timeout is treated as unknown and the update is not acknowledged, so a retry may deliver a duplicate reply.
- No Telegram bot token, live Telegram connection, webhook registration, or production EL_AI deployment is configured or verified.

## Current evidence refresh — 2026-11-20 UTC

- Current repository `main`: `e5eae3b026854aaf2157388cf83d82ffa6106a34`.
- Exact-main GitHub Actions runs completed successfully: CI (run 44637623845), Workflow Security Audit (run 44637623893), and OpenSSF Scorecard (run 44637623829).
- Runtime-level public/owner privacy regression coverage is merged in PR #67. Dependency-aware plan DAGs and bounded root-first advancement are implemented on current `main`; live Telegram acceptance, production deployment, and full release security/dependency review remain unverified.
- The current source already includes owner-scoped task submission, task status/history/result commands, durable task watches, and bounded persisted retry backoff. Further planning work must extend these contracts rather than duplicate them.
- No Telegram bot token is configured, no live Telegram connection or webhook registration is verified, and no production EL_AI deployment is claimed.
- Branch protection for `main` is currently reported as disabled by GitHub metadata. No repository access-control settings were changed.
- This is a source and CI evidence refresh only; it does not claim live runtime acceptance or production readiness.


## Privacy-boundary regression coverage — 2026-11-21 UTC

- PR #67 merged runtime-level tests for ordinary owner-private messages and owner-only Hub result/progress commands.
- The fake public Gateway records every prompt; tests assert that private owner text, Hub result markers, and private task metadata never enter that prompt or a non-owner reply.
- Telegram, Hub, and website Gateway clients are mocked. No live bot token, production endpoint, or device credential is used.
- PR #67 CI passed on Python 3.11 and 3.12, including Ruff, the full pytest suite, and Workflow Security Audit. This is code/CI coverage only; live Telegram acceptance and deployment remain separate release gates.


## Current dependency-aware planning evidence — 2026-11-21 UTC

- Current repository `main`: `ff31209cb1d6c380b8cb3df55de17339353a2fec`.
- `/taskplan` preserves sequential shorthand and accepts bounded explicit 1-based dependency DAGs (2–8 steps). The plan is a local preview; no Hub task is created until the owner explicitly invokes `/taskplanrun PLAN_ID`.
- `/taskplanrun` submits only root steps initially. The polling loop advances eligible steps at its bounded 30-second cadence only after all prerequisites report `succeeded`. Failed, rejected, cancelled, or blocked prerequisites produce durable blocked checkpoints; unavailable Hub status never unlocks a dependent step.
- Stable per-plan/per-step idempotency keys support retries after ambiguous Hub outcomes. Active submitted steps are registered for owner-scoped task-watch notifications. Hub PolicyEngine and approval requirements remain authoritative for each submitted task.
- AI-generated plan previews preserve validated dependency metadata when supplied by the Hub planner. Preview alone never creates or executes tasks; remote provider use remains explicit.
- Backward-compatible task-plan storage accepts the previous version-1 sequential format and the new version-2 dependency fields.
- Exact-main Python 3.11 and 3.12 tests, Workflow Security Audit, and OpenSSF Scorecard completed successfully (runs 44928644306, 44928644348, 44928644369). These are hosted CI results; no local test execution is claimed here.
- No Telegram bot token is configured, no live Telegram connection or webhook registration is verified, and no production EL_AI deployment is claimed.

