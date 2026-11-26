"""Safe dispatch boundary for normalized Telegram Business update events."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from el_ai.telegram.business_connections import (
    BusinessActorContext,
    BusinessConnection,
    BusinessConnectionAuthorizationError,
    BusinessConnectionStore,
)
from el_ai.telegram.updates import ParsedTelegramUpdate


BusinessEventStatus = Literal[
    "connection_enabled",
    "connection_disabled",
    "customer_context_resolved",
    "rejected",
    "ignored",
]


@dataclass(frozen=True, slots=True)
class BusinessEventResult:
    update_id: int
    event_type: str
    status: BusinessEventStatus
    reason: str | None = None
    connection: BusinessConnection | None = None
    actor: BusinessActorContext | None = None


class BusinessEventProcessor:
    """Apply only the safe state transitions for normalized Business events.

    The processor persists connection lifecycle updates and may resolve a read-only
    customer actor. It never executes commands, calls a model, accesses Hub, reads
    owner memory, downloads media, or sends Telegram replies.
    """

    def __init__(
        self,
        store: BusinessConnectionStore,
        owner_ids: frozenset[int] | set[int],
    ) -> None:
        if not isinstance(store, BusinessConnectionStore):
            raise TypeError("A BusinessConnectionStore is required")
        if not isinstance(owner_ids, (set, frozenset)):
            raise ValueError("Configured owner IDs must be a set of numeric IDs")
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in owner_ids
        ):
            raise ValueError("Configured owner IDs must be positive numeric IDs")
        self._store = store
        self._owner_ids = frozenset(owner_ids)

    def process(
        self,
        update: ParsedTelegramUpdate,
        *,
        now: int | None = None,
    ) -> BusinessEventResult:
        """Process one parsed Update without acknowledging persistence failures."""
        if not isinstance(update, ParsedTelegramUpdate):
            raise TypeError("A ParsedTelegramUpdate is required")

        event_type = update.event_type
        event = update.business_event
        if event is None:
            return BusinessEventResult(
                update_id=update.update_id,
                event_type=event_type,
                status="ignored",
                reason=update.ignored_reason or "not_business_event",
            )

        if event_type == "business_connection":
            try:
                connection = self._store.update_from_event(
                    event,
                    owner_ids=self._owner_ids,
                    update_id=update.update_id,
                    now=now,
                )
            except BusinessConnectionAuthorizationError:
                return BusinessEventResult(
                    update_id=update.update_id,
                    event_type=event_type,
                    status="rejected",
                    reason="business_account_not_owner",
                )
            return BusinessEventResult(
                update_id=update.update_id,
                event_type=event_type,
                status=(
                    "connection_enabled"
                    if connection.is_enabled
                    else "connection_disabled"
                ),
                connection=connection,
            )

        if event_type == "business_message":
            actor = self._store.resolve_customer_actor(event)
            if actor is None:
                return BusinessEventResult(
                    update_id=update.update_id,
                    event_type=event_type,
                    status="ignored",
                    reason="connection_or_customer_not_authorized",
                )
            return BusinessEventResult(
                update_id=update.update_id,
                event_type=event_type,
                status="customer_context_resolved",
                actor=actor,
            )

        if event_type in {"edited_business_message", "deleted_business_messages"}:
            return BusinessEventResult(
                update_id=update.update_id,
                event_type=event_type,
                status="ignored",
                reason="non_new_business_message",
            )

        return BusinessEventResult(
            update_id=update.update_id,
            event_type=event_type,
            status="ignored",
            reason="unsupported_business_event",
        )
