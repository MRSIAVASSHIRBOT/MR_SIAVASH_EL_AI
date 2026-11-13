# ADR-0002: Owner-initiated Hub pairing

- **Status:** Accepted; Hub API and owner controls are on main; EL_AI client is on main; live pairing remains unverified
- **Date:** 2026-11-09

## Decision

EL_AI must not store the Hub owner password or reuse an unrestricted owner session. The Hub issues a short-lived, revocable credential bound to this bot instance, Telegram numeric ID and explicit capabilities.

Required properties:
- owner-initiated pairing with a clear consent summary;
- one-time challenge with five-minute expiry and replay prevention;
- credential scope limited to named read/task capabilities;
- Hub-side ownership and authorization on every request;
- 15-minute access token and 30-day revocable refresh token;
- owner-controlled client listing and revocation;
- idempotency keys for side-effecting task requests;
- bounded request/response sizes, rate, execution time and provider budget;
- audit events that exclude tokens, passwords, private prompts and unnecessary content;
- human approval for external publishing, destructive, financial or consequential actions.

## Implementation status

The Hub pairing API, database migration, owner-facing challenge/list/revoke controls, scoped authentication, task and memory routes, idempotent task submission, revocation, token-hash storage and regression tests are merged on Hub `main`. Hub PR [#336](https://github.com/MRSIAVASSHIRBOT/MR_SIAVASH_HUB_AI/pull/336) was merged on 2026-11-09. The later Hub refresh-token hardening is also on `main`: rotated refresh tokens are single-use and reuse is rejected.

EL_AI's scoped Hub client, stable installation identity, OS-backed Keyring credential store, explicit integration opt-in and owner pairing commands are merged on EL_AI `main`. EL_AI PR [#3](https://github.com/MRSIAVASSHIRBOT/MR_SIAVASH_EL_AI/pull/3) introduced the client; PR [#14](https://github.com/MRSIAVASSHIRBOT/MR_SIAVASH_EL_AI/pull/14) added safe persistence of rotated refresh credentials.

Pairing remains opt-in. No live Telegram bot is configured or deployed, and no live end-to-end pairing smoke test has been verified. Live use still requires a running local Hub, owner-initiated pairing and owner-managed Telegram runtime configuration. This documentation does not authorize remote Hub hosting, a public tunnel or production bot deployment.

## Rejected shortcuts

- storing the Hub owner password in EL_AI;
- putting a general Hub owner session token in Telegram bot configuration;
- exposing the local Hub port through a public tunnel;
- trusting Telegram username or a client-supplied role as owner proof;
- bypassing Hub policy or approval checks.
