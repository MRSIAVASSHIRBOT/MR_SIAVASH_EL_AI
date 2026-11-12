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

The implementation provides long polling, owner/public separation, a scoped Hub pairing client, OS-backed credential storage, and a read-only public website assistant client. Public conversations remain disabled by default in EL_AI, and no live Telegram bot is configured or deployed. The MR_SIAVASH_AI production website Gateway is a separate service: its current production deployment (Worker version 14, receiving 100% of traffic as recorded in the EL_AI README on 2026-11-12) has an active Workers AI binding and the configured 90-day support-retention schedule. The scheduled cleanup emitted a completion event on 2026-11-12; that event alone does not establish exact deletion counts or prove every retention outcome. These website Gateway capabilities do not mean the Telegram bot is connected, do not grant EL_AI private Hub access, and do not expose agent execution or approval decisions to Telegram. Private Hub operations remain opt-in and owner-paired.
