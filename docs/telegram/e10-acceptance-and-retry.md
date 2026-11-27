# E10 — Telegram adapter acceptance and retry semantics

Status: implementation evidence exists; phase acceptance remains partial.

## Covered on the source mainline

- Long polling with a persisted update cursor; the cursor advances only after an update is handled or deliberately ignored.
- Bounded parsing of update envelopes and regular messages, including malformed IDs, unsupported variants, invalid Unicode and message-size limits.
- Owner/public command separation and owner-scoped Hub operations.
- Durable outbound delivery state with content digests instead of message text.
- Multi-part responses with UTF-16-aware splitting and per-chunk confirmation.
- Ambiguous send outcomes are quarantined and are not automatically replayed.
- A Bot API 429 is handled as an explicit rejection: valid `parameters.retry_after` is surfaced, only the rejected chunk returns to pending, confirmed earlier chunks remain sent, and the update cursor is not advanced.
- Invalid or missing retry hints use bounded exponential backoff. They do not expose provider response text or bot credentials.

## Automated verification

The repository test suite covers malformed update envelopes, cursor persistence, response splitting, partial delivery, unknown outcomes, idempotent confirmed delivery, 429 retry metadata validation, safe outbox resume, and retry-delay selection. The CI run for the exact PR commit is the acceptance evidence for those source-level checks.

## Operational acceptance still required

1. Configure a dedicated test bot separately from any production bot. Keep its token in a local secret store or untracked environment file; never add it to the repository or send it in chat.
2. Set the test bot owner ID and the minimum required settings in the local environment. Keep public access and Hub integration disabled for the initial transport smoke.
3. Start the local runtime, send a harmless private `/start` and `/help` message from the configured owner, and verify a single response per update.
4. Stop and restart the runtime; verify that previously acknowledged updates are not processed again.
5. Verify that an explicit 429 waits for the server-provided interval and that an ambiguous network interruption is quarantined rather than automatically resent.
6. Record only non-sensitive evidence: test date, application commit SHA, pass/fail outcomes, and sanitized logs. Do not record tokens, chat contents or personal identifiers.

A separate test bot is required to avoid affecting production users. No live Telegram bot was contacted during this source change.

## Deliberately outside this acceptance slice

- Webhook receiver and its TLS, secret-token, replay and request-boundary review.
- General inbound media download/processing and callback-query interaction.
- Live test-bot E2E, which requires an owner-configured isolated test environment.
- Production activation or webhook changes.

These are not marked complete by unit tests or source CI. E10 remains partial until the applicable adapter-state-machine cases, isolated test-bot E2E and operational runbook evidence are accepted.
