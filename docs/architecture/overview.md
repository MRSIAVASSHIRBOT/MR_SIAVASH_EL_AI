# Architecture Overview

## Product boundary

MR_SIAVASH_EL_AI is the Telegram-facing interface, not a second AI execution engine.

- Telegram transport and update lifecycle live here.
- User-to-Telegram identity mapping and public/owner command routing live here.
- Private memory, task state, Agent execution, tool permissions, approval and audit remain owned by MR_SIAVASH_HUB_AI.
- Public website content and public module/status metadata remain owned by MR_SIAVASH_AI.

## Trust zones

1. **Telegram edge:** untrusted inbound updates, bounded parsing, private-chat-only policy.
2. **EL_AI application:** identity checks, rate limiting, command routing and response formatting.
3. **Private Hub zone:** owner-authenticated, policy-controlled API; unavailable until owner pairing is reviewed.
4. **Public website zone:** public-only Gateway endpoints; no private Hub credentials or records.

No shared database, shared session, implicit role assertion, or direct access from a public user to the Hub is permitted.

## Request lifecycle

Telegram update -> structural validation -> private-chat check -> numeric user ID lookup -> rate limit -> command authorization -> capability routing -> (future) scoped Hub request -> Hub policy/approval -> bounded task -> validated result -> Telegram response.

## Runtime modes

- Local development: long polling.
- Production: webhook only after a dedicated deployment review, TLS, webhook secret validation, replay/update handling, rate limits and operational monitoring.
- The first release does not configure a public webhook or deploy infrastructure.

## Current implementation boundary

The initial implementation provides a minimal long-polling transport, owner/public separation, basic commands and tests. Conversation memory, LLM responses, task creation, Agent control, website API calls and private Hub pairing are intentionally not advertised as implemented.
