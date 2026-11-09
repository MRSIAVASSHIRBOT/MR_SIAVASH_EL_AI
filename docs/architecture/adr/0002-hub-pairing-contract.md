# ADR-0002: Owner-initiated Hub pairing

- Status: Proposed; implementation blocked on Hub-side support
- Date: 2026-11-09

## Decision proposal

EL_AI must not store the Hub owner password or reuse an unrestricted owner session. A future pairing flow must issue a short-lived, revocable credential bound to this bot instance and an explicit set of capabilities.

Required properties:
- owner-initiated pairing with a clear consent summary;
- one-time challenge with expiry and replay prevention;
- credential scope limited to named read/task capabilities;
- Hub-side ownership and authorization on every request;
- revocation and rotation;
- idempotency keys for side-effecting requests;
- bounded request/response sizes, rate, execution time and provider budget;
- audit events that exclude tokens, passwords, private prompts and unnecessary content;
- human approval for external publishing, destructive, financial or consequential actions.

## Current blocker

The Hub's current authentication API issues a general owner session after password login. It does not yet expose a bot-specific, narrowly scoped, revocable credential. EL_AI therefore keeps private Hub calls disabled.

## Rejected shortcuts

- storing the Hub owner password in EL_AI;
- putting a long-lived Hub session token in Telegram bot configuration;
- exposing the local Hub port through a public tunnel;
- trusting Telegram username or a client-supplied role as owner proof;
- bypassing Hub policy or approval checks.
