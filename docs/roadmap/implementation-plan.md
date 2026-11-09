# Implementation plan

## Milestone 0 — Secure Telegram transport foundation

Status: implemented in the current feature branch; CI verification pending.

- Strict environment configuration and owner ID parsing.
- Owner-only private-chat handling by default.
- Bounded Telegram long polling with capped retry backoff.
- Sanitized API errors and secret-safe settings representation.
- Offline unit tests and Python 3.11/3.12 CI.
- No persistent memory, LLM, Hub credential or action execution.

## Milestone 1 — Identity and pairing contract

- Define owner identity mapping and account recovery rules.
- Pair EL_AI with Hub through an explicit owner-initiated flow.
- Issue a narrowly scoped server-side credential with audience, expiry and revocation.
- Reject browser-held owner credentials and shared databases.
- Add replay protection and auditable request identifiers.

## Milestone 2 — Read-only Agent interface

- Route requests through Hub's authenticated Agent API.
- Enforce per-user and per-project context scope.
- Return source-backed status and bounded responses.
- Keep private memory and conversation history in Hub only.

## Milestone 3 — Planning and approval workflow

- Display plans and proposed actions as structured objects.
- Require action-bound owner approval for consequential operations.
- Separate proposal, approval, execution and verification states.
- Add idempotency and rollback metadata before any write-capable tool is exposed.

## Milestone 4 — Public experience

- Keep public help and public knowledge separate from owner mode.
- Apply rate limits, abuse controls and privacy disclosure.
- Never expose private project context, memory or tools to public users.

## Milestone 5 — Production delivery

- Add a Telegram webhook with TLS and Telegram secret-token validation.
- Configure secret storage, health checks, deployment rollback and observability redaction.
- Do not deploy or enable public mode until owner approval and release gates pass.
