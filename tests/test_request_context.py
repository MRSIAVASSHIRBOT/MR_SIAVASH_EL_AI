from uuid import uuid4

import pytest

from el_ai.integrations.request_context import (
    canonical_correlation_id,
    current_correlation_id,
    request_correlation_scope,
)


def test_only_canonical_uuid_values_are_accepted():
    value = str(uuid4())
    assert canonical_correlation_id(value) == value
    assert canonical_correlation_id(value.upper()) is None
    assert canonical_correlation_id("telegram-user-123") is None
    assert canonical_correlation_id(None) is None


def test_request_correlation_scope_restores_previous_context():
    outer = str(uuid4())
    inner = str(uuid4())

    with request_correlation_scope(outer):
        assert current_correlation_id() == outer
        with request_correlation_scope(inner):
            assert current_correlation_id() == inner
        assert current_correlation_id() == outer

    assert current_correlation_id() is None


def test_request_correlation_scope_resets_after_exceptions():
    value = str(uuid4())

    with pytest.raises(RuntimeError, match="expected"):
        with request_correlation_scope(value):
            assert current_correlation_id() == value
            raise RuntimeError("expected")

    assert current_correlation_id() is None


def test_invalid_correlation_scope_does_not_bind_arbitrary_text():
    with request_correlation_scope("not-a-canonical-uuid") as value:
        assert value is None
        assert current_correlation_id() is None
