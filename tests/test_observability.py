import json
from datetime import UTC, datetime, timedelta

from el_ai.observability import RuntimeMetrics


class CaptureLogger:
    def __init__(self):
        self.messages = []

    def info(self, message, *args):
        self.messages.append((message, args))


def test_runtime_metrics_count_poll_outcomes_without_retaining_request_data():
    started = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
    metrics = RuntimeMetrics(started_at=started)

    metrics.record_poll(2)
    metrics.record_reply()
    metrics.record_ignored_update()
    metrics.record_telegram_api_error()
    metrics.record_telegram_api_error()
    metrics.record_poll(1)

    snapshot = metrics.snapshot(now=started + timedelta(seconds=17))

    assert snapshot == {
        "event": "telegram_runtime_metrics",
        "polling_status": "healthy",
        "uptime_seconds": 17,
        "last_successful_poll_age_seconds": 0,
        "polls": 2,
        "updates_received": 3,
        "replies_sent": 1,
        "updates_ignored": 1,
        "telegram_api_errors": 2,
        "consecutive_poll_errors": 0,
        "last_successful_poll_at": metrics.last_successful_poll_at.isoformat(),
    }
    assert not {"message", "message_text", "chat_id", "user_id", "update_id"} & snapshot.keys()


def test_runtime_metrics_emit_one_parseable_aggregate_log_record():
    metrics = RuntimeMetrics()
    metrics.record_poll(4)
    metrics.record_reply()
    logger = CaptureLogger()

    emitted = metrics.emit(logger)

    assert len(logger.messages) == 1
    message, args = logger.messages[0]
    assert message == "%s"
    parsed = json.loads(args[0])
    assert parsed == emitted
    assert parsed["event"] == "telegram_runtime_metrics"
    assert all(isinstance(parsed[key], int) for key in (
        "polls",
        "updates_received",
        "replies_sent",
        "updates_ignored",
        "telegram_api_errors",
        "consecutive_poll_errors",
    ))
    assert not any(secret in args[0] for secret in ("access_token", "chat_id", "user_id", "message_text"))


def test_runtime_metrics_distinguish_starting_and_degraded_polling():
    metrics = RuntimeMetrics()
    assert metrics.snapshot()["polling_status"] == "starting"

    metrics.record_telegram_api_error()
    snapshot = metrics.snapshot()
    assert snapshot["polling_status"] == "degraded"
    assert snapshot["consecutive_poll_errors"] == 1
    assert snapshot["last_successful_poll_age_seconds"] is None
