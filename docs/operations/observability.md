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

## Cloudflare bridge worker observability

The signed bridge consumer emits one `telegram_bridge_worker_metrics` JSON
record when it stops. It includes only process-lifetime counts for successful
queue polls, empty polls, claimed and completed jobs, handler failures,
transport failures, shutdown cancellations, uptime, and the age of the last
successful pull. It never records job IDs, Telegram IDs, payloads, replies,
lease tokens, credentials, or response bodies.

When shutdown arrives during application handling, the worker cancels that
handler and deliberately does not acknowledge the lease. The edge queue can
redeliver the job after its lease expires; the consumer does not create a
second local retry queue or claim that the interrupted job completed.

## Hub request correlation

The scoped Hub client emits one `hub_request_completed` structured log event for each outbound attempt. Each event contains only:

- canonical `request_id`, unique to the HTTP operation;
- canonical `correlation_id`, taken from the current Bridge job context when present, otherwise equal to `request_id`;
- HTTP `method`;
- allowlisted `route_template` (task UUIDs are replaced with `{task_id}`);
- `status_code` (null when no HTTP response was received);
- monotonic `duration_ms` (bounded to a non-negative millisecond value);
- coarse `outcome` (`success`, `http_error`, `request_failed`, or `invalid_response`).

The Bridge worker binds the canonical Bridge job UUID in an asynchronous task-local context for the duration of one job handler. Hub calls made within that handler share the same `correlation_id`; separate HTTP operations retain distinct `request_id` values. The context is reset after success, exception, or cancellation. Local polling and non-Bridge work do not invent a Bridge correlation value.

The same request ID is reused for the single permitted 401 refresh/retry of one authorized operation; the refresh request has its own ID. Pairing and refresh operations each receive fresh IDs. Query strings, raw URLs, request/response bodies, Telegram identities, task IDs, client IDs, and credentials are excluded. Correlation IDs are diagnostic metadata only and are not authentication, ownership, authorization, or idempotency values.
