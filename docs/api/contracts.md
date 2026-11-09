# API and Integration Contracts

## Hub pairing contract

The Hub owns Telegram client registration, scopes, token issuance, expiry, revocation and audit.

- Owner creates a challenge bound to a Telegram numeric user ID and a stable EL_AI client UUID.
- Challenge is random, single-use and expires after five minutes; only its SHA-256 hash is stored.
- Exchange issues a 15-minute access token and a 30-day refresh token.
- Hub stores only token hashes. Owner can list and revoke paired clients.
- EL_AI stores raw credentials only in an OS-backed credential manager.
- Every paired request includes the bearer access token and X-Telegram-User-Id.
- Dedicated bot scopes are status:read, tasks:read, tasks:create, and memory:search.
- Agent execution and approval decisions are not in the initial scope set.

## Scoped endpoints

- POST /integrations/telegram/pairing-challenges: owner-authenticated challenge creation.
- POST /integrations/telegram/exchange: single-use challenge exchange.
- POST /integrations/telegram/refresh: refresh an access token using the paired refresh credential.
- GET /integrations/telegram/clients and POST /integrations/telegram/clients/{client_id}/revoke: owner management.
- GET /integrations/telegram/status: requires status:read.
- GET /integrations/telegram/tasks: requires tasks:read and returns only tasks submitted by the exact paired client.
- POST /integrations/telegram/tasks: requires tasks:create and an idempotency key; Hub PolicyEngine remains authoritative.
- POST /integrations/telegram/memory/search: requires memory:search and is owner-scoped.

Telegram credentials do not authenticate against the Hub's general owner-session routes. The Hub derives the owner identity from the paired client record, not from an owner ID supplied by the Telegram request.

## Public website API

EL_AI calls only POST /v1/assistant/chat on the public MR_SIAVASH_AI Gateway. It sends only the current public message; no Telegram user ID, private conversation history, Hub credential, memory or task data is forwarded.

- The Gateway request body is limited to one message of at most 1,200 characters.
- The client uses HTTPS for remote endpoints, a 12-second timeout, no automatic retries and a process-wide request budget below the Gateway's public IP rate limit.
- Only relative source paths resolving beneath the configured public website base URL are shown as links.
- knowledge_fallback is labelled as verified site knowledge, not model-generated AI.
- The production Gateway currently has no Workers AI binding; public assistant responses must not be described as live model inference.

## Versioning

All cross-project contracts must be versioned, schema-validated and covered by positive and negative integration tests before UI copy claims the projects are connected.
