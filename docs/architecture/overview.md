# Architecture Overview

## Product boundary

MR_SIAVASH_EL_AI is the Telegram-facing interface, not a second AI execution engine.

- Telegram transport and update lifecycle live here.
- User-to-Telegram identity mapping, rate limiting and command routing live here.
- Private memory, task state, Agent execution, tool permissions, approval and audit remain owned by MR_SIAVASH_HUB_AI.
- Public website content and public module/status metadata remain owned by MR_SIAVASH_AI.

## Trust zones

1. **Telegram edge:** untrusted inbound updates, bounded parsing, private-chat-only policy.
2. **EL_AI application:** identity checks, per-user rate limiting, command routing and response formatting.
3. **Private Hub zone:** owner-authenticated, policy-controlled API; accessed only after explicit opt-in and owner-initiated pairing.
4. **Public website zone:** public-only Gateway endpoints; no private Hub credentials or records.

No shared database, shared session, implicit role assertion, or direct access from a public user to the Hub is permitted.

## Request lifecycle

Telegram update -> structural validation -> private-chat check -> numeric user ID lookup -> rate limit -> command authorization -> capability routing.

- Public user text -> public MR_SIAVASH_AI Gateway -> grounded answer or explicit knowledge fallback -> validated public source links -> Telegram.
- Owner private commands -> scoped Hub client -> Hub ownership and scope checks -> policy/approval -> bounded task or read -> Telegram.
- Owner can explicitly query public website knowledge with /site; private Hub context is never forwarded to the public Gateway.

## Runtime modes

- Local development: long polling.
- Production: webhook only after a dedicated deployment review, TLS, webhook secret validation, replay/update handling, rate limits and operational monitoring.
- The first release does not configure a public webhook or deploy infrastructure.

## Current implementation boundary

The implementation provides long polling, owner/public separation, a scoped Hub pairing client, OS-backed credential storage, and a read-only public website assistant client. Public conversations remain disabled by default. The production website Gateway currently returns verified knowledge fallback because its production Worker has no Workers AI binding. Agent execution and approval decisions are not exposed to Telegram.
