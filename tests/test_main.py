import asyncio

from el_ai.config import Settings
from el_ai.integrations.secrets import SecretStoreError
from el_ai.main import _build_hub_client, _owner_hub_command, _send_response


def _settings(*, enabled: bool) -> Settings:
    return Settings(
        telegram_bot_token="test-token",
        owner_ids=frozenset({123}),
        hub_api_base_url="http://127.0.0.1:8000",
        hub_integration_enabled=enabled,
    )


def test_disabled_hub_integration_does_not_initialize_keyring(monkeypatch):
    def unexpected_keyring_initialization():
        raise AssertionError("Keyring must not be accessed while Hub integration is disabled")

    monkeypatch.setattr("el_ai.main.KeyringCredentialStore", unexpected_keyring_initialization)
    assert _build_hub_client(_settings(enabled=False), "client-id") is None


def test_enabled_hub_integration_initializes_the_scoped_client(monkeypatch):
    marker = object()
    store = object()
    monkeypatch.setattr("el_ai.main.KeyringCredentialStore", lambda: store)

    def build_client(base_url, client_id, credential_store, *, allow_remote):
        assert base_url == "http://127.0.0.1:8000"
        assert client_id == "client-id"
        assert credential_store is store
        assert allow_remote is False
        return marker

    monkeypatch.setattr("el_ai.main.HubClient", build_client)
    assert _build_hub_client(_settings(enabled=True), "client-id") is marker


def test_unavailable_keyring_fails_closed(monkeypatch):
    def unavailable():
        raise SecretStoreError("test-only failure")

    monkeypatch.setattr("el_ai.main.KeyringCredentialStore", unavailable)
    assert _build_hub_client(_settings(enabled=True), "client-id") is None


def test_pair_command_cannot_bypass_explicit_hub_opt_in():
    class UnexpectedHub:
        async def pair(self, *_args):
            raise AssertionError("Pairing must not run while Hub integration is disabled")

    result = asyncio.run(_owner_hub_command(
        "/pair test-only-code", telegram_user_id=123, update_id=1,
        hub=UnexpectedHub(), settings=_settings(enabled=False)))
    assert result is not None
    assert "غیرفعال" in result


def test_send_response_delivers_every_chunk_without_truncation():
    class FakeAPI:
        def __init__(self):
            self.sent = []

        async def send_message(self, chat_id, text):
            self.sent.append((chat_id, text))

    api = FakeAPI()
    response = "پاسخ طولانی ✨ 😀 " * 1200

    asyncio.run(_send_response(api, 123, response))

    assert len(api.sent) > 1
    assert all(chat_id == 123 for chat_id, _ in api.sent)
    assert "".join(text for _, text in api.sent) == response
    assert all(len(text.encode("utf-16-le")) // 2 <= 4096 for _, text in api.sent)
