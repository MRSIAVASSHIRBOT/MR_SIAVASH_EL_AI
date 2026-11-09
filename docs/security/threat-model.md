# Threat Model

## Assets

- Telegram bot token and future pairing credentials.
- Owner identity and private conversation context.
- Hub memory, task records, files, Agent runs and audit events.
- Public user messages and public knowledge responses.
- Availability and integrity of task/notification delivery.

## Trust assumptions

Telegram updates and all user-supplied content are untrusted. Telegram numeric IDs are identifiers, not cryptographic proof against account takeover. Hub authorization remains authoritative for private capabilities.

## Threats and controls

| Threat | Control |
| --- | --- |
| Public user invokes owner command | Numeric ID allowlist, server-side role check, private-chat-only |
| Group chat leaks private context | Reject all non-private chats in the first release |
| Bot token appears in logs/repository | Environment secret, sanitized errors, no request URL logging, secret fields excluded from Settings representations |
| Token-bearing HTTP exception is exposed | Do not retain HTTP client exceptions as chained contexts |
| Flooding / resource exhaustion | Per-user bounded rate limiter, request timeouts, bounded polling backoff |
| Prompt injection in messages | Treat content as data; future tools are capability allowlisted and Hub-policy checked |
| Replayed or duplicate side effect | Future idempotency key and Hub-side deduplication |
| Credential theft | No Hub password/session in this release; future scoped short-lived credential |
| Cross-user data access | Hub-side ownership checks; no user-supplied owner ID trusted |
| Ambiguous Telegram send timeout | Do not blindly retry; report outcome as unknown in operational logs |
| Sensitive data in telemetry | No message body/token logging by default |

## Release gates

- Public mode remains disabled by default.
- Owner commands require a configured numeric owner ID.
- Hub integration cannot be enabled through environment flags in this release.
- No public webhook, tunnel or production deployment is created by the bootstrap change.
