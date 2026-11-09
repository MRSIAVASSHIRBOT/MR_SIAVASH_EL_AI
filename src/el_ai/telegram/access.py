"""Telegram identity checks and bounded in-memory request throttling."""

from __future__ import annotations

import time
from collections import deque


class AccessController:
    MAX_TRACKED_USERS = 2048

    def __init__(self, owner_ids: frozenset[int], *, public_enabled: bool, rate_limit: int) -> None:
        if rate_limit < 1:
            raise ValueError("rate_limit must be positive")
        self._owner_ids = owner_ids
        self._public_enabled = public_enabled
        self._rate_limit = rate_limit
        self._events: dict[int, deque[float]] = {}

    def is_owner(self, user_id: int | None) -> bool:
        return user_id is not None and user_id in self._owner_ids

    def allow_message(self, *, user_id: int | None, chat_type: str | None) -> bool:
        if user_id is None or chat_type != "private":
            return False
        if not self.is_owner(user_id) and not self._public_enabled:
            return False
        now = time.monotonic()
        events = self._events.get(user_id)
        if events is None:
            if len(self._events) >= self.MAX_TRACKED_USERS:
                oldest_user = next(iter(self._events))
                self._events.pop(oldest_user, None)
            events = deque()
            self._events[user_id] = events
        while events and events[0] <= now - 60:
            events.popleft()
        if len(events) >= self._rate_limit:
            return False
        events.append(now)
        return True
