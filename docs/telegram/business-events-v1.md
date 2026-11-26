
# Telegram Business event contract v1

**Contract:** telegram-business-event.v1  
**Owner:** MR_SIAVASH_EL_AI  
**Source protocol:** Telegram Bot API 10.5, reviewed 2026-11-26  
**Implementation status:** Parser and schema are implemented; Business execution and production delivery are not.

## Scope

This contract normalizes four Telegram update variants:

- business_connection: connection creation, revocation/disablement, or rights changes.
- business_message: a new message in a connected business account.
- edited_business_message: an edit to a message from that connection.
- deleted_business_messages: message deletion metadata from that connection.

The contract files are:
- contracts/telegram-business/v1/manifest.json
- contracts/telegram-business/v1/update.schema.json

The Python parser is src/el_ai/telegram/updates.py.

## Trust and actor rules

A Business connection identifies a business account controlled by the owner. A message sender is a separate customer actor. The two identities must never be collapsed.

- A Business customer is not a Hub owner.
- A Business customer does not inherit owner commands, Hub pairing, Hub scopes, owner memory, device controls, or administration.
- The connection must be known, enabled, and mapped to an owner-authorized Business capability configuration before a future responder handles messages.
- A reply requires the current connection to be enabled and rights.can_reply == true. Missing rights are treated as false.
- Connection updates must replace the cached rights/status snapshot. A disabled or unknown connection cannot reply.
- Per-customer conversation state must be isolated by connection and chat. Do not mix chats, connections or owner sessions.

## Data minimization

The parser returns only a small normalized projection. It does not pass through raw Telegram objects, usernames, names, full profile fields, file IDs or unique file IDs. For media, only a bounded media_kind classification is carried; this version does not download or forward media.

Message text and captions are bounded by UTF-16 length. Business chats must be private chats. Deleted message IDs are bounded and deduplicated.

## Compatibility

Existing regular message parsing remains unchanged in shape. A Business event is returned in ParsedTelegramUpdate.business_event, while ParsedTelegramUpdate.message stays null. This is deliberate: Business events cannot accidentally flow through the existing owner/public MessageRouter.

Malformed Business events retain the Telegram update_id and a stable ignored_reason. An envelope containing more than one recognized update variant is rejected as ambiguous.

## Explicit non-goals for v1

This parser/schema does not:
- reply to a Business customer;
- store connection state persistently;
- call getBusinessConnection;
- create a customer-facing Agent session;
- call the public Website Gateway or private Hub;
- download, inspect or send attachments;
- modify Telegram webhook registration or allowed updates;
- migrate or write to any live D1 database.

## Next acceptance gates

1. Add a persistent, encrypted Business connection registry with status/rights reconciliation and revocation tests.
2. Add a separate Business actor context and per-connection/per-chat conversation store.
3. Define bounded attachment references and a media-processing policy.
4. Extend the edge queue with a distinct Business event envelope and idempotency key.
5. Add worker-consumer tests proving Business messages cannot invoke owner-only commands or Hub scopes.
6. Add exact-connection reply adapters requiring enabled state and can_reply.
7. Verify connection changes, new/edit/delete messages, text, captions, and each supported media type on a non-production Business account.
8. Only after the full acceptance matrix passes, plan a separately approved production rollout.

Telegram references reviewed:
- https://core.telegram.org/bots/api
- https://core.telegram.org/bots/api-changelog
