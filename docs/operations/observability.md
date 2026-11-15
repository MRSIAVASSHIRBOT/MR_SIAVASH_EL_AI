# Telegram runtime observability

The polling process emits a structured `telegram_runtime_metrics` JSON log
record after each successful long-poll response, after Telegram API failures,
and during shutdown.

The record contains only aggregate counters and process timing:

- successful poll count and updates received;
- replies sent and updates ignored;
- Telegram API failure count and current consecutive failure count;
- uptime and the last successful poll timestamp.

It deliberately excludes message text, Telegram user IDs, chat IDs, update IDs,
Hub credentials, and request/response bodies. Metrics remain local to the
configured application logger; no external telemetry service or network
destination is configured by this feature.

The counters are process-lifetime aggregates and reset when the bot restarts.
They are operational signals, not durable billing, audit, or delivery receipts.


## Hub request correlation

The scoped Hub client emits one `hub_request_completed` structured log event for each outbound attempt. Each event contains only:

- canonical `request_id`;
- HTTP `method`;
- allowlisted `route_template` (task UUIDs are replaced with `{task_id}`);
- `status_code` (null when no HTTP response was received);
- coarse `outcome` (`success`, `http_error`, `request_failed`, or `invalid_response`).

The same request ID is reused for the single permitted 401 refresh/retry of one authorized operation; the refresh request has its own ID. Pairing and refresh operations each receive fresh IDs. Query strings, raw URLs, request/response bodies, Telegram identities, task IDs, client IDs, and credentials are excluded. Request IDs are diagnostic metadata only and are not authorization or idempotency values.
