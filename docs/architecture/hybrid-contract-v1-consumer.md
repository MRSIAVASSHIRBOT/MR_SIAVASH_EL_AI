# Hybrid Contract v1 — EL_AI Consumer Notes

**Component role:** Telegram transport, Telegram identity, command routing, and the scoped Hub API client.  
**Canonical contract:** [MR_SIAVASH_HUB_AI — Hybrid Integration Contract v1](https://github.com/MRSIAVASSHIRBOT/MR_SIAVASH_HUB_AI/blob/main/contracts/hybrid/v1/manifest.json)

## Ownership

EL_AI owns Telegram updates, Telegram user/chat identity, command parsing, delivery, and its local operating-system credential-store integration. It does not own Hub memory, task persistence, policy decisions, approvals, or audit records.

## Hub API consumption

- Use `src/el_ai/integrations/hub.py` as the only private Hub API client.
- Use the dedicated `/integrations/telegram` API surface; do not call Hub internal routes.
- Send the paired bearer access token and `X-Telegram-User-Id` header on authenticated operations.
- Treat Hub scopes as authoritative. Never infer additional permissions from Telegram owner status alone.
- Store credentials through the operating-system credential store adapter; never place credentials in URLs, logs, source control, or browser storage.
- Preserve the bounded pagination, result size, and idempotency rules in the canonical manifest.
- Keep private commands fail-closed when pairing is absent, expired, or revoked.

## Security boundaries

- No direct Hub database access.
- No shared database or implicit trust based on network location.
- No private Hub token in Telegram messages, public website requests, or client-side code.
- Public website knowledge queries remain separate from owner-only Hub operations.
- Pairing must remain owner-initiated and bound to the Telegram user ID and this installation's client ID.

## Request correlation status

The proposed implementation is under review in [EL_AI PR #31](https://github.com/MRSIAVASSHIRBOT/MR_SIAVASH_EL_AI/pull/31) and [Hub PR #394](https://github.com/MRSIAVASSHIRBOT/MR_SIAVASH_HUB_AI/pull/394). EL_AI generates opaque UUID request IDs for outbound Hub operations and the public website Gateway; the Hub change validates or replaces inbound IDs and returns the canonical value in `X-Request-ID`.

**These changes are not yet merged as a coordinated contract update.** Until both implementations and the canonical Hub manifest/documentation are merged and verified together, consumers must not rely on end-to-end correlation as a released contract guarantee. Request IDs are diagnostic metadata only; they do not grant permissions or provide idempotency.

## Compatibility

The Hub manifest is the source of truth for endpoint paths, methods, scopes, token lifetimes, and request bounds. Changes that alter authentication, scope meaning, ownership, or request/response semantics require a coordinated major-version migration. The Hub contract PR must merge before relying on its main-branch manifest URL.
