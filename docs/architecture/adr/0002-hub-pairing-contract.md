# ADR-0002: Owner-initiated Hub pairing

- Status: Accepted; implementation in Hub PR #336 and EL_AI feature branch
- Date: 2026-11-09

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

The Hub-side pairing repository, migration, route family and tests are in Hub PR #336. EL_AI's Hub client and OS credential-vault adapter are in branch `feat/hub-pairing-client`. Live pairing remains disabled until both changes are merged, the Hub is running locally and owner-managed Telegram secrets are configured.

## Rejected shortcuts

- storing the Hub owner password in EL_AI;
- putting a general Hub owner session token in Telegram bot configuration;
- exposing the local Hub port through a public tunnel;
- trusting Telegram username or a client-supplied role as owner proof;
- bypassing Hub policy or approval checks.
