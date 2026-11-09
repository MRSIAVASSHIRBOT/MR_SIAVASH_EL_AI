# Implementation Plan

## Milestone 0 — Repository foundation
- [x] Create repository baseline and trust-boundary documentation.
- [x] Define configuration and secret handling.
- [x] Define fail-closed public/owner access defaults.
- [x] Add minimal Telegram long-polling transport.
- [x] Add tests and CI.
- [ ] Verify CI on GitHub Actions.

## Milestone 1 — Production-grade Telegram core
- [ ] Validate Telegram update variants with typed schemas.
- [ ] Add command registration and robust response formatting.
- [ ] Add persistent update cursor and duplicate-update handling.
- [ ] Add structured metrics without message content.
- [ ] Add webhook receiver after deployment threat review.

## Milestone 2 — Identity and owner pairing
- [ ] Add Hub-side bot client registration and revocable scoped credentials.
- [ ] Add one-time owner pairing challenge and explicit consent.
- [ ] Add capability grants and revocation.
- [ ] Add cross-user and replay negative tests.

## Milestone 3 — Public assistant
- [ ] Integrate only the public MR_SIAVASH_AI Gateway contract.
- [ ] Display grounded-source/fallback status honestly.
- [ ] Add public usage quotas and privacy controls.

## Milestone 4 — Memory and planning
- [ ] Delegate private memory to Hub.
- [ ] Add task planning and structured progress messages.
- [ ] Keep public and owner context isolated.

## Milestone 5 — Bounded Agent capabilities
- [ ] Expose named capabilities only.
- [ ] Add idempotency, timeouts, budgets and audit correlation.
- [ ] Require Hub approval for consequential side effects.

## Milestone 6 — Release
- [ ] End-to-end Telegram tests.
- [ ] Security review and dependency audit.
- [ ] Deployment runbook, backup/restore and incident response.
- [ ] Production release and live verification.

## Current truthful status

The repository bootstrap is committed on the feature branch. No bot token has been configured, no live Telegram bot has been connected, and no Hub or website runtime integration is active.
