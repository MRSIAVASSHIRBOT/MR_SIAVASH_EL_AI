
# Telegram Business event contract v1

**Contract:** telegram-business-event.v1  
**Owner:** MR_SIAVASH_EL_AI  
**Source protocol:** Telegram Bot API 10.5, reviewed 2026-11-26  
**Implementation status:** Parser, JSON Schema, owner-bound local registry and isolated processor are implemented. Business Update subscription is now explicitly opt-in for local polling through `TELEGRAM_BUSINESS_UPDATES_ENABLED`; it remains disabled by default and requires configured owner IDs. The current processor only stores connection lifecycle state or resolves a read-only customer actor; it does not call a model, access Hub, or send replies. Cloudflare Bridge integration, Business reply execution, automatic model handling and production delivery are not implemented.

## Scope

This contract normalizes four Telegram update variants:

- business_connection: connection creation, revocation/disablement, or rights changes.
- business_message: a new message in a connected business account.
- edited_business_message: an edit to a message from that connection.
- deleted_business_messages: message deletion metadata from that connection.

The contract files are:
- contracts/telegram-business/v1/manifest.json
- contracts/telegram-business/v1/update.schema.json

The Python parser is src/el_ai/telegram/updates.py. The owner-bound registry is src/el_ai/telegram/business_connections.py; the isolated processor is src/el_ai/telegram/business_processor.py.

## Trust and actor rules

A Business connection identifies a business account controlled by the owner. A message sender is a separate customer actor. The two identities must never be collapsed.

- A Business customer is not a Hub owner.
- A Business customer does not inherit owner commands, Hub pairing, Hub scopes, owner memory, device controls, or administration.
- The local registry accepts a connection only when its business account user ID is in the configured owner ID allowlist. Unknown connections are not resolved.
- Resolving a customer actor requires an enabled connection and rights.can_read_messages == true. A reply additionally requires the current rights.can_reply == true. Missing rights are treated as false.
- Connection updates replace the complete rights/status snapshot. Duplicate or out-of-order Update IDs are ignored; a disabled or unknown connection cannot produce a customer actor.
- Per-customer conversation state must be isolated by connection and chat. Do not mix chats, connections or owner sessions.

## Data minimization

The parser returns only a small normalized projection. It does not pass through raw Telegram objects, usernames, names, full profile fields, file IDs or unique file IDs. It retains the optional is_from_business_account direction flag so outgoing messages from the business account are not treated as customer requests. For media, only a bounded media_kind classification is carried; this version does not download or forward media. The local registry stores connection metadata and known rights in a bounded JSON file using atomic replacement and owner-only file permissions on POSIX systems; no API tokens or session credentials are stored there.

Message text and captions are bounded by UTF-16 length. Business chats must be private chats. Deleted message IDs are bounded and deduplicated.

## Compatibility

Existing regular message parsing remains unchanged in shape. A Business event is returned in ParsedTelegramUpdate.business_event, while ParsedTelegramUpdate.message stays null. This is deliberate: Business events cannot accidentally flow through the existing owner/public MessageRouter.

Malformed Business events retain the Telegram update_id and a stable ignored_reason. An envelope containing more than one recognized update variant is rejected as ambiguous.

## Explicit non-goals for v1

This parser, registry and processor do not:
- reply to a Business customer;
- enable Business Update subscriptions by default; an operator must explicitly set `TELEGRAM_BUSINESS_UPDATES_ENABLED=true` for local polling after the required acceptance gates pass;
- wire Business event processing into the legacy Cloudflare Bridge loop;
- call getBusinessConnection;
- create a customer-facing Agent session;
- call the public Website Gateway or private Hub;
- download, inspect or send attachments;
- modify Telegram webhook registration or allowed updates;
- migrate or write to any live D1 database.

## Next acceptance gates

1. Add per-connection/per-chat isolated customer session state and explicit owner-configured Business capabilities; keep the default capability set empty.
2. Define bounded attachment references and a reviewed media-processing policy.
3. Extend the Cloudflare edge queue with a distinct Business event envelope, durable idempotency and revocation-safe delivery.
4. Add worker-consumer tests proving Business customers cannot invoke owner commands or Hub scopes.
5. Add exact-connection reply adapters that re-check enabled state and can_reply immediately before sending.
6. Verify connection changes, new/edit/delete messages, text, captions and supported media types on a non-production Business account.
7. Run the full offline/negative authorization matrix before enabling the opt-in setting on any non-test runtime.
8. Verify connection creation, rights changes, disablement and duplicate/out-of-order updates with a dedicated non-production Business account.
9. Only after the complete acceptance matrix passes, plan a separately approved production rollout.

Telegram references reviewed:
- https://core.telegram.org/bots/api
- https://core.telegram.org/bots/api-changelog
