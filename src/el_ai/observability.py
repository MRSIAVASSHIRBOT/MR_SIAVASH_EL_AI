"""Privacy-safe aggregate runtime metrics for the Telegram polling process."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass(slots=True)
class RuntimeMetrics:
    """Count runtime outcomes without retaining message, user, chat, or update data."""

    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    polls: int = 0
    updates_received: int = 0
    replies_sent: int = 0
    updates_ignored: int = 0
    telegram_api_errors: int = 0
    consecutive_poll_errors: int = 0
    last_successful_poll_at: datetime | None = None

    def record_poll(self, update_count: int) -> None:
        self.polls += 1
        self.updates_received += max(0, update_count)
        self.consecutive_poll_errors = 0
        self.last_successful_poll_at = datetime.now(UTC)

    def record_reply(self) -> None:
        self.replies_sent += 1

    def record_ignored_update(self) -> None:
        self.updates_ignored += 1

    def record_telegram_api_error(self) -> None:
        self.telegram_api_errors += 1
        self.consecutive_poll_errors += 1

    def snapshot(self, *, now: datetime | None = None) -> dict[str, object]:
        current = now or datetime.now(UTC)
        return {
            "event": "telegram_runtime_metrics",
            "uptime_seconds": max(0, int((current - self.started_at).total_seconds())),
            "polls": self.polls,
            "updates_received": self.updates_received,
            "replies_sent": self.replies_sent,
            "updates_ignored": self.updates_ignored,
            "telegram_api_errors": self.telegram_api_errors,
            "consecutive_poll_errors": self.consecutive_poll_errors,
            "last_successful_poll_at": (
                self.last_successful_poll_at.isoformat()
                if self.last_successful_poll_at is not None
                else None
            ),
        }

    def emit(self, logger: logging.Logger) -> dict[str, object]:
        """Write one structured aggregate event; never serialize request payloads."""
        payload = self.snapshot()
        logger.info(
            "%s",
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        )
        return payload
