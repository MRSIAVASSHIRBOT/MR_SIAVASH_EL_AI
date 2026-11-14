import asyncio
import json

from el_ai.config import Settings
from el_ai.integrations.secrets import SecretStoreError
from el_ai.main import (
    _build_hub_client,
    _format_result,
    _owner_hub_command,
    _runtime_health_response,
    _send_response,
)
from el_ai.observability import RuntimeMetrics


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


def test_format_result_preserves_complete_large_hub_payload():
    payload = {"items": [{"text": "😀" * 2000}]}

    rendered = _format_result(payload)

    assert json.loads(rendered) == payload


def test_pair_command_uses_real_line_breaks():
    class FakeHub:
        async def pair(self, code, telegram_user_id):
            assert code == "test-only-code"
            assert telegram_user_id == 123
            return {"client_id": "client-123", "scopes": ["tasks.read", "memory.read"]}

    result = asyncio.run(_owner_hub_command(
        "/pair test-only-code",
        telegram_user_id=123,
        update_id=2,
        hub=FakeHub(),
        settings=_settings(enabled=True),
    ))

    assert result.splitlines() == [
        "اتصال امن با Hub برقرار شد.",
        "Client ID: client-123",
        "Scopes: tasks.read, memory.read",
    ]


def test_task_status_command_fetches_only_a_valid_task_identifier():
    task_id = "12345678-1234-4234-8234-123456789abc"

    class FakeHub:
        async def get_task(self, telegram_user_id, requested_id):
            assert telegram_user_id == 123
            assert requested_id == task_id
            return {"task_id": task_id, "status": "succeeded"}

    rendered = asyncio.run(_owner_hub_command(
        f"/taskstatus {task_id}",
        telegram_user_id=123,
        update_id=3,
        hub=FakeHub(),
        settings=_settings(enabled=True),
    ))
    assert json.loads(rendered) == {"task_id": task_id, "status": "succeeded"}

    invalid = asyncio.run(_owner_hub_command(
        "/taskstatus not-a-uuid",
        telegram_user_id=123,
        update_id=4,
        hub=FakeHub(),
        settings=_settings(enabled=True),
    ))
    assert "UUID" in invalid


def test_tasks_command_uses_cursor_and_returns_a_clear_next_page_command():
    class FakeHub:
        async def list_tasks(self, telegram_user_id, *, cursor=None, limit=20):
            assert telegram_user_id == 123
            assert cursor == "cursor-current"
            assert limit == 20
            return {
                "tasks": [{"task_id": "task-1"}],
                "count": 1,
                "has_more": True,
                "next_cursor": "cursor-next",
            }

    rendered = asyncio.run(_owner_hub_command(
        "/tasks cursor-current",
        telegram_user_id=123,
        update_id=5,
        hub=FakeHub(),
        settings=_settings(enabled=True),
    ))
    payload, next_page = rendered.split("\n\nصفحه بعد: ", 1)
    assert json.loads(payload) == {
        "tasks": [{"task_id": "task-1"}],
        "count": 1,
        "has_more": True,
    }
    assert next_page == "/tasks cursor-next"


def test_tasks_command_handles_invalid_cursor_without_crashing():
    class FakeHub:
        async def list_tasks(self, *_args, **_kwargs):
            raise ValueError("invalid cursor")

    rendered = asyncio.run(_owner_hub_command(
        "/tasks invalid",
        telegram_user_id=123,
        update_id=6,
        hub=FakeHub(),
        settings=_settings(enabled=True),
    ))
    assert "نشانگر صفحه" in rendered


def test_runtime_health_response_contains_only_aggregate_polling_state():
    metrics = RuntimeMetrics()
    metrics.record_poll(3)
    metrics.record_reply()

    payload = json.loads(_runtime_health_response(metrics))

    assert payload["polling_status"] == "healthy"
    assert payload["polls"] == 1
    assert payload["updates_received"] == 3
    assert payload["replies_sent"] == 1
    assert not {"message", "chat_id", "user_id", "update_id", "access_token"} & payload.keys()
