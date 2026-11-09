from uuid import UUID

from el_ai.integrations.identity import load_or_create_client_id


def test_client_identity_is_stable_between_restarts(tmp_path):
    first = load_or_create_client_id(tmp_path)
    second = load_or_create_client_id(tmp_path)
    assert first == second
    assert str(UUID(first)) == first
    assert (tmp_path / "client-id").exists()
