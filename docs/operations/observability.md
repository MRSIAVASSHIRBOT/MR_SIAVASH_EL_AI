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
