# Threat Model

## Assets

- Telegram bot token and scoped Hub access/refresh credentials.
- Owner identity and private conversation context.
- Hub memory, task records, files, Agent runs and audit events.
- Public user messages and public knowledge responses.
- Availability and integrity of task/notification delivery.

## Trust assumptions

Telegram updates and all user-supplied content are untrusted. Telegram numeric IDs are identifiers, not cryptographic proof against account takeover. Hub authorization remains authoritative for private capabilities. The public website Gateway is a separate public trust zone and must never receive private Hub context.

## Threats and controls

| Threat | Control |
| --- | --- |
| Public user invokes owner command | Numeric ID allowlist, server-side role check, private-chat-only |
| Group chat leaks private context | Reject all non-private chats in the first release |
| Bot token appears in logs/repository | Environment secret, sanitized errors, no request URL logging, secret fields excluded from Settings representations |
| Token-bearing HTTP exception is exposed | Do not retain HTTP client exceptions as chained contexts |
| Flooding / resource exhaustion | Per-user bounded rate limiter, public Gateway process-wide request budget, request timeouts and bounded polling backoff |
| Prompt injection in messages | Treat content as data; future tools are capability allowlisted and Hub-policy checked |
| Replayed or duplicate side effect | Hub-side idempotency keys and task deduplication |
| Credential theft | OS-backed Keyring storage, short-lived scoped access tokens, Hub-side token hashing, expiry and owner revocation |
| Cross-user data access | Hub-side ownership checks; Telegram task listing scoped to exact paired client; no user-supplied owner ID trusted |
| Public Gateway leaks private context | Send only the current public message; never forward Telegram IDs, Hub tokens, memory, task details or private history |
| Malicious source link from Gateway | Accept only relative paths that resolve beneath the configured public website base URL |
| Oversized or malformed Gateway response | Bounded request/response sizes, schema checks, short timeout, no automatic retries |
| Ambiguous Telegram send timeout | Do not blindly retry; report outcome as unknown in operational logs |
| Sensitive data in telemetry | No message body/token logging by default |

## Release gates

- Public mode remains disabled by default.
- Owner commands require a configured numeric owner ID.
- Hub integration is disabled by default; client initialization and /pair require HUB_INTEGRATION_ENABLED=true and a valid HUB_API_BASE_URL.
- Remote Hub URLs require HTTPS and explicit HUB_ALLOW_REMOTE=true.
- Public Gateway calls use HTTPS, a fixed public API contract, no private context and a bounded request budget.
- No public webhook, tunnel or production bot deployment is created by the bootstrap change.
