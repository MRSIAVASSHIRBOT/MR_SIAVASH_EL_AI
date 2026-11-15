# Hybrid Contract v1 — EL_AI Consumer Notes

**Component role:** Telegram transport, Telegram identity, command routing, and the scoped Hub API client.  
**Canonical contract:** [MR_SIAVASH_HUB_AI — Hybrid Integration Contract v1](https://github.com/MRSIAVASSHIRBOT/MR_SIAVASH_HUB_AI/blob/main/contracts/hybrid/v1/manifest.json)

## Ownership

EL_AI owns Telegram updates, Telegram user/chat identity, command parsing, delivery, and its local operating-system credential-store integration. It does not own Hub memory, task persistence, policy decisions, approvals, or audit records.

## Hub API consumption

- Use `src/el_ai/integrations/hub.py` as the only private Hub API client.
- Use the dedicated `/integrations/telegram` API surface; do not call Hub internal routes.
- Send the paired bearer access token and `X-Telegram-User-Id` header on authenticated operations.
- Treat Hub scopes as authoritative. Never infer additional permissions from Telegram owner status alone. The authenticated Hub owner can explicitly update an active paired client's scope set; the bot does not request or grant scopes itself.
- Store credentials through the operating-system credential store adapter; never place credentials in URLs, logs, source control, or browser storage.
- Preserve the bounded pagination, result size, and idempotency rules in the canonical manifest.
- Keep private commands fail-closed when pairing is absent, expired, or revoked. Video commands require `video:read`; pending-only cancellation additionally requires `video:cancel`.\n- Keep `/videoproviders`, `/videohistory`, `/videostatus`, and `/videocancel` owner-only. The provider catalog is read-only; cancellation must never claim that active or ambiguous external work stopped.

## Security boundaries

- No direct Hub database access.
- No shared database or implicit trust based on network location.
- No private Hub token in Telegram messages, public website requests, or client-side code.
- Public website knowledge queries remain separate from owner-only Hub operations.
- Pairing must remain owner-initiated and bound to the Telegram user ID and this installation's client ID.

## Request correlation status

The coordinated request-correlation implementation is merged into the default branches through [EL_AI PR #31](https://github.com/MRSIAVASSHIRBOT/MR_SIAVASH_EL_AI/pull/31) and [Hub PR #394](https://github.com/MRSIAVASSHIRBOT/MR_SIAVASH_HUB_AI/pull/394). The canonical Hub v1 manifest marks `X-Request-ID` as standardized.

- EL_AI assigns a canonical lowercase UUIDv4 to each logical Hub operation and to each independent public Website Gateway request.
- Hub validates canonical lowercase UUIDs (versions 1–8), replaces missing or invalid values with a server-generated UUIDv4, stores the canonical value in request state, and returns it in `X-Request-ID` on handled HTTP responses.
- On one authorized Hub operation receiving HTTP 401, EL_AI refreshes credentials using a separate request ID, then replays the original operation at most once using the original request ID. A second 401 is surfaced; no generic retries or backoff are performed.
- Hub logs contain `request_id`, HTTP `method`, route template, and `status_code`; EL_AI logs contain those fields plus an `outcome`. Task IDs are represented as route placeholders.
- Query strings, request bodies, Telegram identities, client IDs, credentials, and raw URLs are excluded from correlation logs.
- Public Website Gateway request IDs are independent; they are not forwarded to the private Hub and do not imply a shared request context.

Request IDs are diagnostic metadata only. They do not grant permissions or provide idempotency. The public Gateway remains public-only and cannot call private Hub routes or hold Hub credentials.

## Compatibility

The Hub manifest is the source of truth for endpoint paths, methods, scopes, token lifetimes, and request bounds. Changes that alter authentication, scope meaning, ownership, or request/response semantics require a coordinated major-version migration. The Hub contract PR must merge before relying on its main-branch manifest URL.
