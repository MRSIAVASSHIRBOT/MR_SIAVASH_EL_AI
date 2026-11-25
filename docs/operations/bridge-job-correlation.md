# X7 — Bridge job correlation contract

## Purpose

A Telegram Bridge job is a stable unit of work. Its canonical UUID is created by the edge Worker and remains the same while the lease is retried. EL_AI uses that UUID as a cross-service correlation identifier while preserving a distinct request identifier for every Hub HTTP operation.

## Contract

- The Bridge worker binds the canonical `job_id` to the current asynchronous handler task through a `ContextVar` scope.
- Context is task-local, not process-global. Concurrent handlers cannot overwrite one another's value, and the scope resets after success, exception, or cancellation.
- The Hub client sends the stable value as `X-Correlation-ID` only when it is a canonical UUID.
- Each HTTP operation retains its own canonical `X-Request-ID`. A retry of the same HTTP operation reuses that request ID according to the existing client behavior; a different operation receives a different request ID.
- Local Telegram polling and non-Bridge work do not invent a Bridge correlation ID.
- Arbitrary text, Telegram user IDs, chat IDs, message text, payload fields and lease tokens are never accepted as correlation values or added to logs.

## Hub integration

The matching Hub middleware change is required to validate the canonical `X-Correlation-ID`, keep it separate from `X-Request-ID`, and attach it to structured request completion logs. The correlation value is diagnostic metadata only: it is not an authentication, authorization, idempotency, or ownership signal.

## Validation

Tests cover canonical UUID validation, nested context restoration, cleanup after exceptions, Bridge worker task isolation, and propagation on both pairing and authenticated Hub calls. This is a correlation slice, not a claim of a complete four-project distributed trace.

## Rollback

Revert the request context module, Hub client header/log propagation, Bridge worker scope, tests and this document. Existing request ID generation and Bridge lease behavior remain unchanged.
