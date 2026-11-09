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
- [ ] Validate Telegram update variants with typed schemas.
- [ ] Add persistent update cursor and duplicate-update handling.
- [ ] Add structured metrics without message content.
- [ ] Add webhook receiver after deployment threat review.

## Milestone 2 — Identity and owner pairing
- [x] Implement Hub-side bot client registration and revocable scoped credentials in Hub PR #336 (pending merge).
- [x] Add one-time owner pairing challenge and Telegram identity binding in Hub PR #336.
- [x] Add capability scopes, client listing and revocation in Hub PR #336.
- [x] Add EL_AI Hub client with OS credential-vault storage in this feature branch.
- [x] Add tests for pairing, expiry, scope enforcement, refresh and revocation.
- [ ] Verify both project PRs and merge the integration client.
- [ ] Complete a live local pairing test after owner-managed runtime secrets are configured.

## Milestone 3 — Public assistant
- [ ] Integrate only the public MR_SIAVASH_AI Gateway contract.
- [ ] Display grounded-source/fallback status honestly.
- [ ] Add public usage quotas and privacy controls.

## Milestone 4 — Memory and planning
- [x] Add owner-scoped memory search through the Hub API contract.
- [ ] Add task planning and structured progress messages.
- [ ] Keep public and owner context isolated.

## Milestone 5 — Bounded Agent capabilities
- [ ] Expose named Agent capabilities only after a separate scope and policy review.
- [ ] Add idempotency, timeouts, budgets and audit correlation.
- [ ] Require Hub approval for consequential side effects.

## Milestone 6 — Release
- [ ] End-to-end Telegram tests.
- [ ] Security review and dependency audit.
- [ ] Deployment runbook, backup/restore and incident response.
- [ ] Production release and live verification.

## Current truthful status

The Telegram foundation is merged and its CI passed (14 tests on Python 3.11 and 3.12). The Hub pairing API and EL_AI pairing client are in separate feature branches under review. No bot token has been configured, no live Telegram bot has been connected, and no production deployment has occurred.
