"""Context-local correlation identifiers for cross-service request tracing."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import UUID

_current_correlation_id: ContextVar[str | None] = ContextVar(
    "el_ai_request_correlation_id",
    default=None,
)


def canonical_correlation_id(value: object) -> str | None:
    """Return a canonical UUID string or None; arbitrary text is never propagated."""
    if not isinstance(value, str) or len(value) != 36:
        return None
    try:
        parsed = UUID(value)
    except (AttributeError, TypeError, ValueError):
        return None
    return value if str(parsed) == value else None


def current_correlation_id() -> str | None:
    """Read the correlation ID bound to the current asynchronous task."""
    return _current_correlation_id.get()


@contextmanager
def request_correlation_scope(value: object) -> Iterator[str | None]:
    """Bind one canonical job/request ID and always restore the prior task context."""
    correlation_id = canonical_correlation_id(value)
    if correlation_id is None:
        yield None
        return

    token = _current_correlation_id.set(correlation_id)
    try:
        yield correlation_id
    finally:
        _current_correlation_id.reset(token)
