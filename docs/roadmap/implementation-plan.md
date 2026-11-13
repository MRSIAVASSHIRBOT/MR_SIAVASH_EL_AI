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
- [ ] Validate Telegram update variants with typed schemas.
- [ ] Add persistent update cursor and duplicate-update handling.
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
- [ ] Verify live production Gateway response from the bot runtime.
- [ ] Add a broader public help and knowledge evaluation set.

## Milestone 4 — Memory and planning
- [x] Add owner-scoped memory search through the Hub API contract.
- [x] Add bounded task submission through Hub PolicyEngine and idempotency.
- [ ] Add task planning and structured progress messages.
- [ ] Keep public and owner context isolated in end-to-end tests.

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

The Telegram foundation, Hub pairing client, explicit opt-in and secret-redaction hardening are merged and tested. Hub pairing APIs, owner UI, and per-client task isolation are merged and tested. The public website assistant integration is in a feature PR with CI running on Python 3.11 and 3.12. Public conversations and Hub access remain disabled by default. No bot token has been configured, no live Telegram bot has been connected, and no production bot deployment has occurred.
