import asyncio
import json
import pytest
import time

from el_ai.config import Settings
from el_ai.integrations.hub import HubClientError
from el_ai.integrations.secrets import SecretStoreError
from el_ai.main import (
    main,
    _build_hub_client,
    _bridge_job_command,
    _bridge_update_id,
    _format_result,
    _owner_hub_command,
    _runtime_health_response,
    _register_command_menu,
    run,
    OWNER_HUB_COMMANDS,
    OWNER_BOT_COMMANDS,
    _poll_task_watches,
    _send_response,
)
from el_ai.observability import RuntimeMetrics
from el_ai.telegram.task_watch import TaskWatchStore
from el_ai.telegram.task_plan import TaskPlanStore
from el_ai.telegram.bridge import BridgeJob
from el_ai.telegram.api import TelegramAPIError


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

    def build_client(
        base_url,
        client_id,
        credential_store,
        *,
        allow_remote,
        allow_render_private_network,
    ):
        assert base_url == "http://127.0.0.1:8000"
        assert client_id == "client-id"
        assert credential_store is store
        assert allow_remote is False
        assert allow_render_private_network is False
        return marker

    monkeypatch.setattr("el_ai.main.HubClient", build_client)
    assert _build_hub_client(_settings(enabled=True), "client-id") is marker


def test_render_private_hub_policy_is_passed_to_the_scoped_client(monkeypatch):
    marker = object()
    store = object()
    monkeypatch.setattr("el_ai.main.KeyringCredentialStore", lambda: store)

    def build_client(
        base_url,
        client_id,
        credential_store,
        *,
        allow_remote,
        allow_render_private_network,
    ):
        assert base_url == "http://mr-siavash-hub-ai:10000"
        assert client_id == "client-render-private"
        assert credential_store is store
        assert allow_remote is False
        assert allow_render_private_network is True
        return marker

    monkeypatch.setattr("el_ai.main.HubClient", build_client)
    settings = Settings(
        telegram_bot_token=None,
        owner_ids=frozenset({123}),
        hub_api_base_url="http://mr-siavash-hub-ai:10000",
        hub_integration_enabled=True,
        hub_allow_remote=False,
        hub_allow_render_private_network=True,
    )

    assert _build_hub_client(settings, "client-render-private") is marker


def test_unavailable_keyring_fails_closed(monkeypatch):
    def unavailable():
        raise SecretStoreError("test-only failure")

    monkeypatch.setattr("el_ai.main.KeyringCredentialStore", unavailable)
    with pytest.raises(SecretStoreError):
        _build_hub_client(_settings(enabled=True), "client-id")


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
        async def list_tasks(self, telegram_user_id, *, cursor=None, limit=20, status=None):
            assert telegram_user_id == 123
            assert cursor == "cursor-current"
            assert limit == 20
            assert status is None
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



def test_task_result_command_returns_terminal_result_and_handles_pending_state():
    task_id = "12345678-1234-4234-8234-123456789abc"

    class FakeHub:
        async def get_task_result(self, telegram_user_id, requested_id):
            assert telegram_user_id == 123
            assert requested_id == task_id
            return {"result": {"message": "done"}}

    rendered = asyncio.run(_owner_hub_command(
        f"/taskresult {task_id}",
        telegram_user_id=123,
        update_id=30,
        hub=FakeHub(),
        settings=_settings(enabled=True),
    ))
    assert json.loads(rendered) == {"result": {"message": "done"}}

    class PendingHub:
        async def get_task_result(self, *_args):
            from el_ai.integrations.hub import HubClientError
            raise HubClientError("not ready", status_code=409)

    pending = asyncio.run(_owner_hub_command(
        f"/taskresult {task_id}",
        telegram_user_id=123,
        update_id=31,
        hub=PendingHub(),
        settings=_settings(enabled=True),
    ))
    assert "هنوز آماده نیست" in pending


def test_task_cancel_command_uses_valid_uuid_and_explains_pending_only_policy():
    task_id = "12345678-1234-4234-8234-123456789abc"

    class FakeHub:
        async def cancel_task(self, telegram_user_id, requested_id):
            assert telegram_user_id == 123
            assert requested_id == task_id
            return {"task_id": task_id, "status": "cancelled"}

    rendered = asyncio.run(_owner_hub_command(
        f"/taskcancel {task_id}",
        telegram_user_id=123,
        update_id=32,
        hub=FakeHub(),
        settings=_settings(enabled=True),
    ))
    assert json.loads(rendered)["status"] == "cancelled"

    class RunningHub:
        async def cancel_task(self, *_args):
            raise HubClientError("not cancellable", status_code=409)

    blocked = asyncio.run(_owner_hub_command(
        f"/taskcancel {task_id}",
        telegram_user_id=123,
        update_id=33,
        hub=RunningHub(),
        settings=_settings(enabled=True),
    ))
    assert "فقط وظایف در صف" in blocked

    invalid = asyncio.run(_owner_hub_command(
        "/taskcancel not-a-uuid",
        telegram_user_id=123,
        update_id=34,
        hub=FakeHub(),
        settings=_settings(enabled=True),
    ))
    assert "UUID" in invalid


def test_tasks_command_filters_by_status_and_keeps_filter_in_next_page():
    class FakeHub:
        async def list_tasks(self, telegram_user_id, *, cursor=None, limit=20, status=None):
            assert telegram_user_id == 123
            assert cursor == "cursor-current"
            assert limit == 20
            assert status == "queued"
            return {
                "tasks": [{"task_id": "queued-task", "status": "queued"}],
                "count": 1,
                "has_more": True,
                "next_cursor": "cursor-next",
            }

    rendered = asyncio.run(_owner_hub_command(
        "/tasks queued cursor-current",
        telegram_user_id=123,
        update_id=35,
        hub=FakeHub(),
        settings=_settings(enabled=True),
    ))
    payload, next_page = rendered.split("\n\nصفحه بعد: ", 1)
    assert json.loads(payload)["tasks"][0]["status"] == "queued"
    assert next_page == "/tasks queued cursor-next"


def test_task_history_command_uses_a_valid_uuid_and_returns_events():
    task_id = "12345678-1234-4234-8234-123456789abc"

    class FakeHub:
        async def get_task_history(self, telegram_user_id, requested_id, *, limit=20, cursor=None):
            assert telegram_user_id == 123
            assert requested_id == task_id
            assert limit == 20
            assert cursor == "cursor-current"
            return {
                "task_id": task_id,
                "events": [{"event_type": "task.submitted", "outcome": "queued"}],
                "count": 1,
                "has_more": True,
                "next_cursor": "cursor-next",
            }

    rendered = asyncio.run(_owner_hub_command(
        f"/taskhistory {task_id} cursor-current",
        telegram_user_id=123,
        update_id=36,
        hub=FakeHub(),
        settings=_settings(enabled=True),
    ))
    payload, next_page = rendered.split("\n\nصفحه بعد: ", 1)
    assert json.loads(payload)["events"][0]["event_type"] == "task.submitted"
    assert next_page == f"/taskhistory {task_id} cursor-next"

    invalid = asyncio.run(_owner_hub_command(
        "/taskhistory not-a-uuid",
        telegram_user_id=123,
        update_id=37,
        hub=FakeHub(),
        settings=_settings(enabled=True),
    ))
    assert "UUID" in invalid


def test_owner_hub_dispatch_allowlist_covers_all_implemented_task_commands():
    assert {"/tasks", "/taskstatus", "/taskhistory", "/taskresult", "/taskcancel", "/memoryentry", "/memoryapprove"} <= OWNER_HUB_COMMANDS



def test_main_dispatches_read_only_doctor_without_loading_runtime_settings(monkeypatch):
    import sys

    import pytest

    from el_ai import doctor

    calls = []
    monkeypatch.setattr(sys, "argv", ["el-ai", "doctor", "--json"])
    monkeypatch.setattr(doctor, "main", lambda *, json_output=False: calls.append(json_output) or 0)

    with pytest.raises(SystemExit) as exc:
        main()

    assert exc.value.code == 0
    assert calls == [True]


def test_video_provider_command_reports_catalog_without_claiming_generation_started():
    class FakeHub:
        async def list_video_providers(self, telegram_user_id):
            assert telegram_user_id == 123
            return {
                "providers": [],
                "count": 0,
                "generation_available": False,
                "reason_code": "provider_unavailable",
                "execution": "not_started",
            }

    rendered = asyncio.run(_owner_hub_command(
        "/videoproviders", telegram_user_id=123, update_id=70,
        hub=FakeHub(), settings=_settings(enabled=True)
    ))
    payload = json.loads(rendered)
    assert payload["generation_available"] is False
    assert payload["execution"] == "not_started"


def test_video_history_command_preserves_cursor_and_hides_internal_cursor_payload():
    class FakeHub:
        async def list_video_generations(self, telegram_user_id, *, cursor=None, limit=10):
            assert telegram_user_id == 123
            assert cursor == "cursor-current"
            assert limit == 10
            return {
                "items": [{"generation_id": "generation-1", "status": "queued"}],
                "count": 1,
                "has_more": True,
                "next_cursor": "cursor-next",
            }

    rendered = asyncio.run(_owner_hub_command(
        "/videohistory cursor-current", telegram_user_id=123, update_id=71,
        hub=FakeHub(), settings=_settings(enabled=True)
    ))
    payload, next_page = rendered.split("\n\nصفحه بعد: ", 1)
    assert json.loads(payload) == {
        "items": [{"generation_id": "generation-1", "status": "queued"}],
        "count": 1,
        "has_more": True,
    }
    assert next_page == "/videohistory cursor-next"


def test_video_status_command_validates_uuid_and_returns_owner_scoped_status():
    generation_id = "12345678-1234-4234-8234-123456789abc"

    class FakeHub:
        async def get_video_generation(self, telegram_user_id, requested_id):
            assert telegram_user_id == 123
            assert requested_id == generation_id
            return {"generation_id": generation_id, "status": "succeeded"}

    rendered = asyncio.run(_owner_hub_command(
        f"/videostatus {generation_id}", telegram_user_id=123, update_id=72,
        hub=FakeHub(), settings=_settings(enabled=True)
    ))
    assert json.loads(rendered)["status"] == "succeeded"

    invalid = asyncio.run(_owner_hub_command(
        "/videostatus invalid", telegram_user_id=123, update_id=73,
        hub=FakeHub(), settings=_settings(enabled=True)
    ))
    assert "UUID" in invalid


def test_video_cancel_command_never_claims_active_provider_work_was_cancelled():
    generation_id = "12345678-1234-4234-8234-123456789abc"

    class FakeHub:
        async def cancel_video_generation(self, telegram_user_id, requested_id):
            assert telegram_user_id == 123
            assert requested_id == generation_id
            raise HubClientError("active provider work", status_code=409)

    rendered = asyncio.run(_owner_hub_command(
        f"/videocancel {generation_id}", telegram_user_id=123, update_id=74,
        hub=FakeHub(), settings=_settings(enabled=True)
    ))
    assert "پیش از ارسال" in rendered


def test_command_menu_registers_public_commands_and_owner_only_commands_per_chat():
    class FakeAPI:
        def __init__(self):
            self.calls = []

        async def set_my_commands(self, commands, *, scope=None):
            self.calls.append((commands, scope))

    api = FakeAPI()
    asyncio.run(_register_command_menu(api, {900, 100}))

    assert api.calls[0][1] == {"type": "all_private_chats"}
    assert [call[1]["chat_id"] for call in api.calls[1:]] == [100, 900]
    public_names = {item["command"] for item in api.calls[0][0]}
    owner_names = {item["command"] for item in api.calls[1][0]}
    assert public_names == {"start", "help"}
    assert {"videoproviders", "videohistory", "videostatus", "videocancel"} <= owner_names


def test_command_menu_registration_continues_after_one_owner_scope_failure():
    class FakeAPI:
        def __init__(self):
            self.calls = []

        async def set_my_commands(self, commands, *, scope=None):
            self.calls.append(scope)
            if scope == {"type": "chat", "chat_id": 100}:
                raise TelegramAPIError("chat scope unavailable")

    api = FakeAPI()
    asyncio.run(_register_command_menu(api, {100, 200}))

    assert api.calls == [
        {"type": "all_private_chats"},
        {"type": "chat", "chat_id": 100},
        {"type": "chat", "chat_id": 200},
    ]


def test_task_watch_commands_register_list_and_remove_only_the_calling_owner(tmp_path):
    task_id = "12345678-1234-4234-8234-123456789abc"
    store = TaskWatchStore(tmp_path / "task-watches.json")
    store.load()

    class FakeHub:
        async def get_task(self, telegram_user_id, requested_id):
            assert telegram_user_id == 123
            assert requested_id == task_id
            return {"task_id": task_id, "status": "running"}

    hub = FakeHub()
    registered = asyncio.run(_owner_hub_command(
        f"/taskwatch {task_id}",
        telegram_user_id=123,
        update_id=40,
        hub=hub,
        settings=_settings(enabled=True),
        task_watches=store,
    ))
    assert "پایش وضعیت وظیفه فعال شد" in registered
    assert len(store.list_for_user(123)) == 1
    assert store.list_for_user(456) == ()

    listed = asyncio.run(_owner_hub_command(
        "/taskwatches",
        telegram_user_id=123,
        update_id=41,
        hub=hub,
        settings=_settings(enabled=True),
        task_watches=store,
    ))
    assert json.loads(listed)["watches"] == [{"task_id": task_id, "status": "running"}]

    removed = asyncio.run(_owner_hub_command(
        f"/taskunwatch {task_id}",
        telegram_user_id=123,
        update_id=42,
        hub=hub,
        settings=_settings(enabled=True),
        task_watches=store,
    ))
    assert "متوقف شد" in removed
    assert store.list() == ()


def test_task_watch_command_rejects_terminal_tasks(tmp_path):
    task_id = "12345678-1234-4234-8234-123456789abc"
    store = TaskWatchStore(tmp_path / "task-watches.json")
    store.load()

    class FakeHub:
        async def get_task(self, *_args):
            return {"status": "succeeded"}

    result = asyncio.run(_owner_hub_command(
        f"/taskwatch {task_id}",
        telegram_user_id=123,
        update_id=43,
        hub=FakeHub(),
        settings=_settings(enabled=True),
        task_watches=store,
    ))
    assert "وضعیت نهایی" in result
    assert store.list() == ()


def test_task_watch_poll_notifies_status_changes_and_removes_terminal_watches(tmp_path):
    task_id = "12345678-1234-4234-8234-123456789abc"
    store = TaskWatchStore(tmp_path / "task-watches.json")
    store.load()
    store.add(task_id, 123, "running")

    class FakeHub:
        async def get_task(self, telegram_user_id, requested_id):
            assert telegram_user_id == 123
            assert requested_id == task_id
            return {"status": "succeeded"}

    class FakeAPI:
        def __init__(self):
            self.sent = []

        async def send_message(self, chat_id, text):
            self.sent.append((chat_id, text))

    api = FakeAPI()
    asyncio.run(_poll_task_watches(api, FakeHub(), store))

    assert len(api.sent) == 1
    assert api.sent[0][0] == 123
    assert "موفق" in api.sent[0][1]
    assert task_id in api.sent[0][1]
    assert store.list() == ()


def test_task_watch_poll_keeps_checkpoint_when_telegram_delivery_fails(tmp_path):
    task_id = "12345678-1234-4234-8234-123456789abc"
    store = TaskWatchStore(tmp_path / "task-watches.json")
    store.load()
    store.add(task_id, 123, "queued")

    class FakeHub:
        async def get_task(self, *_args):
            return {"status": "running"}

    class FailingAPI:
        async def send_message(self, *_args):
            raise TelegramAPIError("simulated failure")

    asyncio.run(_poll_task_watches(FailingAPI(), FakeHub(), store, now=1000))
    watch = store.list_for_user(123)[0]
    assert watch.last_status == "queued"
    assert watch.retry_count == 1
    assert watch.next_attempt_at == 1030


def test_task_watch_commands_are_owner_menu_only():
    commands = {item["command"] for item in OWNER_BOT_COMMANDS}
    assert {"taskwatch", "taskunwatch", "taskwatches"} <= commands
    assert {"/taskwatch", "/taskunwatch", "/taskwatches"} <= OWNER_HUB_COMMANDS
    assert {"taskplanwatch", "taskplancancel"} <= commands
    assert {"/taskplanwatch", "/taskplancancel"} <= OWNER_HUB_COMMANDS


def test_video_route_preview_command_is_read_only_and_requires_explicit_remote_consents():
    calls = []

    class FakeHub:
        async def preview_video_route(self, telegram_user_id, **payload):
            calls.append((telegram_user_id, payload))
            return {
                "decision": {"selected": False, "reason": "no_compatible_provider"},
                "execution": "not_started",
                "provider_submission_started": False,
            }

    hub = FakeHub()
    local = asyncio.run(_owner_hub_command(
        "/videoroute 10 9:16",
        telegram_user_id=123,
        update_id=80,
        hub=hub,
        settings=_settings(enabled=True),
    ))
    assert json.loads(local)["execution"] == "not_started"
    assert calls[-1] == (123, {
        "duration_seconds": 10,
        "aspect_ratio": "9:16",
        "resolution": "720p",
        "remote_processing_approved": False,
        "remote_spend_approved": False,
        "max_remote_cost_usd": 0.0,
    })

    hosted_preview = asyncio.run(_owner_hub_command(
        "/videoroute 10 9:16 720p remote-ok spend-ok 0.50",
        telegram_user_id=123,
        update_id=81,
        hub=hub,
        settings=_settings(enabled=True),
    ))
    assert json.loads(hosted_preview)["provider_submission_started"] is False
    assert calls[-1][1]["remote_processing_approved"] is True
    assert calls[-1][1]["remote_spend_approved"] is True
    assert calls[-1][1]["max_remote_cost_usd"] == 0.5

    before = len(calls)
    invalid = asyncio.run(_owner_hub_command(
        "/videoroute 10 9:16 720p remote-ok",
        telegram_user_id=123,
        update_id=82,
        hub=hub,
        settings=_settings(enabled=True),
    ))
    assert "روش استفاده" in invalid
    assert len(calls) == before


def test_video_route_preview_is_registered_only_for_owner_commands():
    assert "/videoroute" in OWNER_HUB_COMMANDS
    assert "videoroute" in {item["command"] for item in OWNER_BOT_COMMANDS}



def test_task_watch_poll_skips_watches_until_persisted_retry_deadline(tmp_path):
    task_id = "12345678-1234-4234-8234-123456789abc"
    store = TaskWatchStore(tmp_path / "task-watches.json")
    store.load()
    store.add(task_id, 123, "running")
    store.defer(task_id, 123, 1000)
    calls = []

    class FakeHub:
        async def get_task(self, *_args):
            calls.append("called")
            return {"status": "running"}

    class FakeAPI:
        async def send_message(self, *_args):
            raise AssertionError("An unchanged status must not send a notification")

    api = FakeAPI()
    hub = FakeHub()
    asyncio.run(_poll_task_watches(api, hub, store, now=1029))
    assert calls == []
    asyncio.run(_poll_task_watches(api, hub, store, now=1030))
    assert calls == ["called"]
    watch = store.list_for_user(123)[0]
    assert watch.retry_count == 0
    assert watch.next_attempt_at == 0


def test_task_watch_poll_defers_transient_hub_failures(tmp_path):
    task_id = "12345678-1234-4234-8234-123456789abc"
    store = TaskWatchStore(tmp_path / "task-watches.json")
    store.load()
    store.add(task_id, 123, "queued")

    class FailingHub:
        async def get_task(self, *_args):
            raise HubClientError("temporary failure", status_code=503)

    class FakeAPI:
        async def send_message(self, *_args):
            raise AssertionError("A failed Hub lookup must not notify")

    asyncio.run(_poll_task_watches(FakeAPI(), FailingHub(), store, now=500))
    watch = store.list_for_user(123)[0]
    assert watch.last_status == "queued"
    assert watch.retry_count == 1
    assert watch.next_attempt_at == 530



def test_memory_dream_command_uses_read_only_hub_preview():
    class FakeHub:
        async def preview_memory_dream(self, telegram_user_id, *, limit=100):
            assert telegram_user_id == 123
            assert limit == 100
            return {
                "engine": "deterministic_memory_review_v1",
                "proposal_count": 0,
                "state_changed": False,
                "proposals": [],
            }

    rendered = asyncio.run(_owner_hub_command(
        "/memorydream",
        telegram_user_id=123,
        update_id=99,
        hub=FakeHub(),
        settings=_settings(enabled=True),
    ))
    assert json.loads(rendered)["state_changed"] is False
    assert "/memorydream" in OWNER_HUB_COMMANDS



def test_memory_validation_command_uses_read_only_hub_preview():
    class FakeHub:
        async def preview_memory_validation(self, telegram_user_id, *, limit=100):
            assert telegram_user_id == 123
            assert limit == 100
            return {
                "engine": "deterministic_memory_validation_v1",
                "contradiction_count": 0,
                "unverified_count": 0,
                "state_changed": False,
            }

    rendered = asyncio.run(_owner_hub_command(
        "/memorycheck",
        telegram_user_id=123,
        update_id=100,
        hub=FakeHub(),
        settings=_settings(enabled=True),
    ))
    assert json.loads(rendered)["state_changed"] is False
    assert "/memorycheck" in OWNER_HUB_COMMANDS


def test_memory_entry_and_explicit_approval_commands_are_owner_scoped():
    from el_ai.integrations.hub import HubClientError

    memory_id = "12345678-1234-4234-8234-123456789abc"

    class FakeHub:
        async def get_memory_entry(self, telegram_user_id, requested_id):
            assert telegram_user_id == 123
            assert requested_id == memory_id
            return {
                "memory_id": memory_id,
                "category": "preferences",
                "content": "Concise summaries are preferred",
                "verification_status": "user_authored",
            }

        async def approve_memory_entry(self, telegram_user_id, requested_id):
            assert telegram_user_id == 123
            assert requested_id == memory_id
            return {"memory_id": memory_id, "changed": True}

    inspected = asyncio.run(_owner_hub_command(
        f"/memoryentry {memory_id}",
        telegram_user_id=123,
        update_id=80,
        hub=FakeHub(),
        settings=_settings(enabled=True),
    ))
    assert json.loads(inspected)["content"] == "Concise summaries are preferred"

    approved = asyncio.run(_owner_hub_command(
        f"/memoryapprove {memory_id}",
        telegram_user_id=123,
        update_id=81,
        hub=FakeHub(),
        settings=_settings(enabled=True),
    ))
    assert "تأیید صریح مالک" in approved

    invalid = asyncio.run(_owner_hub_command(
        "/memoryapprove not-a-uuid",
        telegram_user_id=123,
        update_id=82,
        hub=FakeHub(),
        settings=_settings(enabled=True),
    ))
    assert "UUID" in invalid

    class ScopeDeniedHub(FakeHub):
        async def approve_memory_entry(self, telegram_user_id, requested_id):
            raise HubClientError("scope denied", status_code=403)

    denied = asyncio.run(_owner_hub_command(
        f"/memoryapprove {memory_id}",
        telegram_user_id=123,
        update_id=83,
        hub=ScopeDeniedHub(),
        settings=_settings(enabled=True),
    ))
    assert "memory:write" in denied

def test_task_progress_command_renders_bounded_timeline_and_safe_next_page():
    task_id = "12345678-1234-4234-8234-123456789abc"

    class FakeHub:
        async def get_task(self, telegram_user_id, requested_id):
            assert telegram_user_id == 123
            assert requested_id == task_id
            return {
                "task_id": task_id,
                "task_type": "demo.echo",
                "status": "running",
                "created_at": "2026-11-20T10:00:00+00:00",
                "updated_at": "2026-11-20T10:05:00+00:00",
            }

        async def get_task_history(self, telegram_user_id, requested_id, *, limit=20, cursor=None):
            assert telegram_user_id == 123
            assert requested_id == task_id
            assert limit == 10
            assert cursor is None
            return {
                "events": [
                    {
                        "event_type": "worker.task_started",
                        "outcome": "running",
                        "created_at": "2026-11-20T10:05:00+00:00",
                        "metadata": {"private_payload": "must-not-appear"},
                    },
                    {
                        "event_type": "task.submitted",
                        "outcome": "queued",
                        "created_at": "2026-11-20T10:00:00+00:00",
                        "metadata": {"internal": "hidden"},
                    },
                ],
                "has_more": True,
                "next_cursor": "cursor-next_123",
            }

    rendered = asyncio.run(_owner_hub_command(
        f"/taskprogress {task_id}",
        telegram_user_id=123,
        update_id=101,
        hub=FakeHub(),
        settings=_settings(enabled=True),
    ))

    assert "گزارش پیشرفت وظیفه" in rendered
    assert "وضعیت: در حال اجرا" in rendered
    assert "demo.echo" in rendered
    assert "اجرای وظیفه آغاز شد" in rendered
    assert "وظیفه ثبت شد" in rendered
    assert "must-not-appear" not in rendered
    assert "internal" not in rendered
    assert f"/taskhistory {task_id} cursor-next_123" in rendered
    assert "/taskprogress" in OWNER_HUB_COMMANDS
    assert any(item["command"] == "taskprogress" for item in OWNER_BOT_COMMANDS)


def test_task_progress_command_validates_ids_and_handles_missing_tasks():
    from el_ai.integrations.hub import HubClientError

    class FakeHub:
        async def get_task(self, *_args):
            raise HubClientError("not found", status_code=404)

    invalid = asyncio.run(_owner_hub_command(
        "/taskprogress not-a-uuid",
        telegram_user_id=123,
        update_id=102,
        hub=FakeHub(),
        settings=_settings(enabled=True),
    ))
    assert "UUID" in invalid

    missing = asyncio.run(_owner_hub_command(
        "/taskprogress 12345678-1234-4234-8234-123456789abc",
        telegram_user_id=123,
        update_id=103,
        hub=FakeHub(),
        settings=_settings(enabled=True),
    ))
    assert "وظیفه پیدا نشد" in missing


def test_task_plan_preview_is_local_owner_scoped_and_does_not_submit_tasks(tmp_path):
    from el_ai.telegram.task_plan import TaskPlanStore

    store = TaskPlanStore(tmp_path / "task-plans.json")

    class FakeHub:
        async def create_task(self, *_args, **_kwargs):
            raise AssertionError("Preview must not submit Hub tasks")

    preview = asyncio.run(_owner_hub_command(
        "/taskplan demo.echo | prepare inputs ; run validation ; summarize results",
        telegram_user_id=123,
        update_id=110,
        hub=FakeHub(),
        settings=_settings(enabled=True),
        task_plans=store,
    ))
    plan_id = preview.split("شناسه طرح: ", 1)[1].splitlines()[0]
    assert "هیچ وظیفه‌ای به Hub ارسال نشده است" in preview
    assert "/taskplanrun " + plan_id in preview
    assert store.get_for_user(plan_id, 123).submitted_count == 0
    assert store.get_for_user(plan_id, 456) is None

    listed = asyncio.run(_owner_hub_command(
        "/taskplans",
        telegram_user_id=123,
        update_id=111,
        hub=FakeHub(),
        settings=_settings(enabled=True),
        task_plans=store,
    ))
    payload = json.loads(listed)
    assert payload["count"] == 1
    assert payload["plans"][0]["submitted_steps"] == 0
    assert "/taskplan" in OWNER_HUB_COMMANDS
    assert "/taskplanrun" in OWNER_HUB_COMMANDS
    assert any(item["command"] == "taskplanrun" for item in OWNER_BOT_COMMANDS)


def test_task_plan_submission_resumes_after_ambiguous_hub_outcome_without_duplicates(tmp_path):
    from uuid import NAMESPACE_URL, uuid5

    from el_ai.telegram.task_plan import TaskPlanStore
    from el_ai.telegram.task_plan_orchestrator import advance_ready_plan_steps
    from el_ai.telegram.task_watch import TaskWatchStore

    store = TaskPlanStore(tmp_path / "task-plans.json")
    watches = TaskWatchStore(tmp_path / "task-watches.json")

    class FakeHub:
        def __init__(self):
            self.records = {}
            self.tasks = {}
            self.calls = []
            self.ambiguous_once = True

        async def create_task(self, telegram_user_id, task_type, description, *, idempotency_key):
            assert telegram_user_id == 123
            assert task_type == "demo.echo"
            self.calls.append(idempotency_key)
            if idempotency_key in self.records:
                return self.records[idempotency_key]
            result = {
                "task_id": str(uuid5(NAMESPACE_URL, idempotency_key)),
                "status": "queued",
                "description": description,
            }
            self.records[idempotency_key] = result
            self.tasks[result["task_id"]] = {"status": "queued"}
            if idempotency_key.endswith("-1") and self.ambiguous_once:
                self.ambiguous_once = False
                raise HubClientError("ambiguous network outcome")
            return result

        async def get_task(self, telegram_user_id, task_id):
            assert telegram_user_id == 123
            return self.tasks[task_id]

    hub = FakeHub()
    preview = asyncio.run(_owner_hub_command(
        "/taskplan demo.echo | first step ; second step ; third step",
        telegram_user_id=123,
        update_id=112,
        hub=hub,
        settings=_settings(enabled=True),
        task_plans=store,
    ))
    plan_id = preview.split("شناسه طرح: ", 1)[1].splitlines()[0]

    interrupted = asyncio.run(_owner_hub_command(
        f"/taskplanrun {plan_id}",
        telegram_user_id=123,
        update_id=113,
        hub=hub,
        settings=_settings(enabled=True),
        task_plans=store,
        task_watches=watches,
    ))
    assert "0 از 3" in interrupted
    assert store.get_for_user(plan_id, 123).submitted_count == 0

    resumed = asyncio.run(_owner_hub_command(
        f"/taskplanrun {plan_id}",
        telegram_user_id=123,
        update_id=114,
        hub=hub,
        settings=_settings(enabled=True),
        task_plans=store,
        task_watches=watches,
    ))
    assert "مراحل ریشهٔ طرح به Hub ارسال شدند" in resumed
    plan = store.get_for_user(plan_id, 123)
    assert plan is not None and plan.submitted_count == 1
    first_task_id = plan.steps[0].task_id
    assert first_task_id is not None

    hub.tasks[first_task_id] = {"status": "succeeded"}
    first_advance = asyncio.run(advance_ready_plan_steps(hub, store, watches, {123}))
    assert [(event.kind, event.step_index) for event in first_advance] == [("submitted", 1)]
    plan = store.get_for_user(plan_id, 123)
    assert plan is not None and plan.submitted_count == 2
    second_task_id = plan.steps[1].task_id
    assert second_task_id is not None

    hub.tasks[second_task_id] = {"status": "succeeded"}
    second_advance = asyncio.run(advance_ready_plan_steps(hub, store, watches, {123}))
    assert [(event.kind, event.step_index) for event in second_advance] == [("submitted", 2)]
    plan = store.get_for_user(plan_id, 123)
    assert plan is not None and plan.submitted_count == 3
    assert len(hub.records) == 3

    repeated = asyncio.run(_owner_hub_command(
        f"/taskplanrun {plan_id}",
        telegram_user_id=123,
        update_id=115,
        hub=hub,
        settings=_settings(enabled=True),
        task_plans=store,
        task_watches=watches,
    ))
    assert "مراحل ریشهٔ طرح به Hub ارسال شدند" in repeated
    assert len(hub.records) == 3
    assert hub.calls == [
        f"telegram-plan-{plan_id}-1",
        f"telegram-plan-{plan_id}-1",
        f"telegram-plan-{plan_id}-2",
        f"telegram-plan-{plan_id}-3",
    ]



def test_task_plan_commands_reject_invalid_input_and_foreign_plan_access(tmp_path):
    from el_ai.telegram.task_plan import TaskPlanStore

    store = TaskPlanStore(tmp_path / "task-plans.json")

    class FakeHub:
        async def create_task(self, *_args, **_kwargs):
            raise AssertionError("Invalid or foreign plan must not submit")

    invalid = asyncio.run(_owner_hub_command(
        "/taskplan invalid type | one ; two",
        telegram_user_id=123,
        update_id=116,
        hub=FakeHub(),
        settings=_settings(enabled=True),
        task_plans=store,
    ))
    assert "طرح معتبر نیست" in invalid
    assert store.list_for_user(123) == ()

    preview = asyncio.run(_owner_hub_command(
        "/taskplan demo.echo | one ; two",
        telegram_user_id=123,
        update_id=117,
        hub=FakeHub(),
        settings=_settings(enabled=True),
        task_plans=store,
    ))
    plan_id = preview.split("شناسه طرح: ", 1)[1].splitlines()[0]
    foreign = asyncio.run(_owner_hub_command(
        f"/taskplanrun {plan_id}",
        telegram_user_id=456,
        update_id=118,
        hub=FakeHub(),
        settings=_settings(enabled=True),
        task_plans=store,
    ))
    assert "پیدا نشد" in foreign
    assert store.get_for_user(plan_id, 123).submitted_count == 0



def test_task_plan_status_reports_each_step_without_exposing_task_payloads(tmp_path):
    from el_ai.telegram.task_plan import TaskPlanStore

    store = TaskPlanStore(tmp_path / "task-plans.json")
    plan = store.create(123, "demo.echo", ["first", "second", "third"])
    first_id = "12345678-1234-4234-8234-123456789abc"
    second_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    store.mark_submitted(plan.plan_id, 123, 0, first_id)
    store.mark_submitted(plan.plan_id, 123, 1, second_id)

    class FakeHub:
        async def get_task(self, telegram_user_id, task_id):
            assert telegram_user_id == 123
            if task_id == first_id:
                return {"status": "running", "result": "private-result"}
            if task_id == second_id:
                return {"status": "succeeded", "output": "private-output"}
            raise AssertionError("Unexpected task ID")

    rendered = asyncio.run(_owner_hub_command(
        f"/taskplanstatus {plan.plan_id}",
        telegram_user_id=123,
        update_id=120,
        hub=FakeHub(),
        settings=_settings(enabled=True),
        task_plans=store,
    ))
    assert "2 از 3" in rendered
    assert "در حال اجرا" in rendered
    assert "موفق" in rendered
    assert "در انتظار موفقیت پیش‌نیازها" in rendered
    assert "private-result" not in rendered
    assert "private-output" not in rendered
    assert "/taskplanstatus" in OWNER_HUB_COMMANDS
    assert any(item["command"] == "taskplanstatus" for item in OWNER_BOT_COMMANDS)


def test_task_plan_watch_registers_all_active_steps_atomically_and_skips_terminal_steps(tmp_path):
    from el_ai.telegram.task_plan import TaskPlanStore

    plan_store = TaskPlanStore(tmp_path / "task-plans.json")
    watch_store = TaskWatchStore(tmp_path / "task-watches.json")
    plan = plan_store.create(123, "demo.echo", ["first", "second", "third"])
    task_ids = [
        "12345678-1234-4234-8234-123456789abc",
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    ]
    for index, task_id in enumerate(task_ids):
        plan_store.mark_submitted(plan.plan_id, 123, index, task_id)

    class FakeHub:
        async def get_task(self, telegram_user_id, task_id):
            assert telegram_user_id == 123
            return {
                task_ids[0]: {"status": "running"},
                task_ids[1]: {"status": "succeeded"},
                task_ids[2]: {"status": "queued"},
            }[task_id]

    rendered = asyncio.run(_owner_hub_command(
        f"/taskplanwatch {plan.plan_id}",
        telegram_user_id=123,
        update_id=130,
        hub=FakeHub(),
        settings=_settings(enabled=True),
        task_plans=plan_store,
        task_watches=watch_store,
    ))

    assert "پایش جدید: 2" in rendered
    assert "مراحل نهایی: 1" in rendered
    assert {item.task_id for item in watch_store.list_for_user(123)} == {task_ids[0], task_ids[2]}
    repeated = asyncio.run(_owner_hub_command(
        f"/taskplanwatch {plan.plan_id}",
        telegram_user_id=123,
        update_id=131,
        hub=FakeHub(),
        settings=_settings(enabled=True),
        task_plans=plan_store,
        task_watches=watch_store,
    ))
    assert "پایش جدید: 0" in repeated
    assert "از قبل فعال: 2" in repeated


def test_task_plan_watch_keeps_partial_availability_visible_without_partial_storage_failure(tmp_path):
    from el_ai.telegram.task_plan import TaskPlanStore

    plan_store = TaskPlanStore(tmp_path / "task-plans.json")
    watch_store = TaskWatchStore(tmp_path / "task-watches.json")
    plan = plan_store.create(123, "demo.echo", ["first", "second"])
    task_ids = [
        "12345678-1234-4234-8234-123456789abc",
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    ]
    for index, task_id in enumerate(task_ids):
        plan_store.mark_submitted(plan.plan_id, 123, index, task_id)

    class FakeHub:
        async def get_task(self, telegram_user_id, task_id):
            if task_id == task_ids[0]:
                return {"status": "running"}
            raise HubClientError("not found", status_code=404)

    rendered = asyncio.run(_owner_hub_command(
        f"/taskplanwatch {plan.plan_id}",
        telegram_user_id=123,
        update_id=132,
        hub=FakeHub(),
        settings=_settings(enabled=True),
        task_plans=plan_store,
        task_watches=watch_store,
    ))

    assert "پایش جدید: 1" in rendered
    assert "در دسترس نبود: 1" in rendered
    assert [item.task_id for item in watch_store.list_for_user(123)] == [task_ids[0]]


def test_task_plan_cancel_requires_confirmation_and_continues_after_partial_failures(tmp_path):
    from el_ai.telegram.task_plan import TaskPlanStore

    plan_store = TaskPlanStore(tmp_path / "task-plans.json")
    plan = plan_store.create(123, "demo.echo", ["first", "second", "third", "not submitted"])
    task_ids = [
        "12345678-1234-4234-8234-123456789abc",
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    ]
    for index, task_id in enumerate(task_ids):
        plan_store.mark_submitted(plan.plan_id, 123, index, task_id)

    class FakeHub:
        def __init__(self):
            self.calls = []

        async def cancel_task(self, telegram_user_id, task_id):
            assert telegram_user_id == 123
            self.calls.append(task_id)
            if task_id == task_ids[0]:
                return {"status": "cancelled"}
            if task_id == task_ids[1]:
                raise HubClientError("not cancellable", status_code=409)
            raise HubClientError("not found", status_code=404)

    hub = FakeHub()
    missing_confirmation = asyncio.run(_owner_hub_command(
        f"/taskplancancel {plan.plan_id}",
        telegram_user_id=123,
        update_id=133,
        hub=hub,
        settings=_settings(enabled=True),
        task_plans=plan_store,
    ))
    assert "CONFIRM" in missing_confirmation
    assert hub.calls == []

    rendered = asyncio.run(_owner_hub_command(
        f"/taskplancancel {plan.plan_id} CONFIRM",
        telegram_user_id=123,
        update_id=134,
        hub=hub,
        settings=_settings(enabled=True),
        task_plans=plan_store,
    ))
    assert len(hub.calls) == 3
    assert "لغوشده: 1 از 3" in rendered
    assert "درحال‌اجرا یا نهایی" in rendered
    assert "پیدا نشد" in rendered
    assert "/taskplanstatus " + plan.plan_id in rendered


def test_task_watch_notifications_include_safe_progress_timeline(tmp_path):
    task_id = "12345678-1234-4234-8234-123456789abc"
    store = TaskWatchStore(tmp_path / "task-watches.json")
    store.load()
    store.add(task_id, 123, "queued")

    class FakeHub:
        async def get_task(self, telegram_user_id, requested_id):
            assert telegram_user_id == 123
            assert requested_id == task_id
            return {
                "task_id": task_id,
                "task_type": "demo.echo",
                "status": "running",
                "updated_at": "2026-11-20T10:05:00+00:00",
            }

        async def get_task_history(self, telegram_user_id, requested_id, *, limit=20, cursor=None):
            assert telegram_user_id == 123
            assert requested_id == task_id
            assert limit == 5
            return {
                "events": [{
                    "event_type": "worker.task_started",
                    "outcome": "running",
                    "created_at": "2026-11-20T10:05:00+00:00",
                    "metadata": {"private_payload": "must-not-appear"},
                }],
                "has_more": False,
                "next_cursor": None,
            }

    class FakeAPI:
        def __init__(self):
            self.sent = []

        async def send_message(self, chat_id, text):
            self.sent.append((chat_id, text))

    api = FakeAPI()
    asyncio.run(_poll_task_watches(api, FakeHub(), store, now=100))
    assert len(api.sent) == 1
    assert api.sent[0][0] == 123
    assert "گزارش پیشرفت وظیفه" in api.sent[0][1]
    assert "اجرای وظیفه آغاز شد" in api.sent[0][1]
    assert "must-not-appear" not in api.sent[0][1]
    assert store.list_for_user(123)[0].last_status == "running"


def test_ai_agent_plan_command_uses_local_by_default_and_never_submits_tasks(tmp_path):
    from el_ai.telegram.task_plan import TaskPlanStore

    store = TaskPlanStore(tmp_path / "agent-plans.json")
    calls = []

    class FakeHub:
        async def preview_agent_plan(self, telegram_user_id, goal, *, provider_mode):
            calls.append((telegram_user_id, goal, provider_mode))
            return {
                "provider_mode": provider_mode,
                "provider": "local-test",
                "steps": [
                    {"description": "نیازمندی‌ها را مشخص کن"},
                    {"description": "پیاده‌سازی و آزمایش را انجام بده"},
                ],
                "execution_started": False,
                "tasks_created": 0,
            }

        async def create_task(self, *_args, **_kwargs):
            raise AssertionError("AI plan preview must never submit Hub tasks")

    hub = FakeHub()
    local = asyncio.run(_owner_hub_command(
        "/agentplan demo.build | یک ابزار ساده برای مدیریت یادداشت بساز",
        telegram_user_id=123,
        update_id=130,
        hub=hub,
        settings=_settings(enabled=True),
        task_plans=store,
    ))
    plan_id = local.split("شناسه طرح: ", 1)[1].splitlines()[0]
    assert calls == [(123, "یک ابزار ساده برای مدیریت یادداشت بساز", "local")]
    assert "هیچ وظیفه‌ای ایجاد یا اجرا نشده است" in local
    assert "/taskplanrun " + plan_id in local
    assert store.get_for_user(plan_id, 123).submitted_count == 0

    remote = asyncio.run(_owner_hub_command(
        "/agentplan remote demo.build | یک ابزار را بررسی کن",
        telegram_user_id=123,
        update_id=131,
        hub=hub,
        settings=_settings(enabled=True),
        task_plans=store,
    ))
    assert calls[-1] == (123, "یک ابزار را بررسی کن", "remote")
    assert "ممکن است هزینهٔ ارائه‌دهنده اعمال شود" in remote
    assert "/agentplan" in OWNER_HUB_COMMANDS
    assert any(item["command"] == "agentplan" for item in OWNER_BOT_COMMANDS)


def test_ai_agent_plan_command_rejects_oversized_goal_before_provider_call(tmp_path):
    from el_ai.telegram.task_plan import TaskPlanStore

    calls = []

    class FakeHub:
        async def preview_agent_plan(self, *_args, **_kwargs):
            calls.append(True)
            raise AssertionError("oversized goals must be rejected before provider use")

    response = asyncio.run(_owner_hub_command(
        "/agentplan demo.build | " + ("x" * 1_001),
        telegram_user_id=123,
        update_id=132,
        hub=FakeHub(),
        settings=_settings(enabled=True),
        task_plans=TaskPlanStore(tmp_path / "agent-plans.json"),
    ))

    assert "حداکثر ۱۰۰۰ نویسه" in response
    assert calls == []



def _bridge_job(payload, *, chat_id=123, user_id=123, job_id="c4b7d9a1-8f21-4c62-9a31-2b6d8e0f4a55"):
    return BridgeJob(
        job_id=job_id,
        chat_id=chat_id,
        telegram_user_id=user_id,
        payload=payload,
        attempts=1,
        lease_token="a" * 64,
    )


def test_bridge_job_uses_unique_stable_idempotency_seeds():
    first = _bridge_job({"command": "status"})
    second = _bridge_job(
        {"command": "status"},
        job_id="d4b7d9a1-8f21-4c62-9a31-2b6d8e0f4a56",
    )

    first_seed = _bridge_update_id(first)
    second_seed = _bridge_update_id(second)

    assert first_seed == _bridge_update_id(first)
    assert first_seed != second_seed
    assert isinstance(first_seed, int)
    assert isinstance(second_seed, int)


def test_bridge_job_maps_only_the_documented_owner_commands():
    owners = frozenset({123})
    assert _bridge_job_command(_bridge_job({"command": "status"}), owners) == "/hub"
    assert _bridge_job_command(_bridge_job({"command": "tasks"}), owners) == "/tasks"
    assert _bridge_job_command(
        _bridge_job({"command": "pair", "pairing_code": "P" * 32}), owners
    ) == "/pair " + "P" * 32


def test_bridge_job_rejects_unknown_extra_fields_and_non_private_chats():
    owners = frozenset({123})
    assert _bridge_job_command(
        _bridge_job({"command": "status", "extra": "ignored"}), owners
    ) is None
    assert _bridge_job_command(
        _bridge_job({"command": "memory"}), owners
    ) is None
    assert _bridge_job_command(
        _bridge_job({"command": ["status"]}), owners
    ) is None
    assert _bridge_job_command(
        _bridge_job({"command": "status"}, chat_id=-100, user_id=123), owners
    ) is None
    assert _bridge_job_command(
        _bridge_job({"command": "status"}, user_id=999), owners
    ) is None
    assert _bridge_job_command(
        _bridge_job({"command": "pair", "pairing_code": "invalid code"}), owners
    ) is None
    assert _bridge_job_command(
        _bridge_job({"command": "pair", "pairing_code": "short"}), owners
    ) is None



def test_bridge_transport_does_not_start_local_telegram_polling(monkeypatch):
    settings = Settings(
        telegram_bot_token=None,
        owner_ids=frozenset({123}),
        telegram_transport="cloudflare_bridge",
        telegram_bridge_url="https://bridge.example.test",
        telegram_bridge_shared_secret="x" * 40,
        hub_api_base_url="https://hub.example.test",
        hub_integration_enabled=True,
    )
    called = []

    async def bridge_runtime(received):
        called.append(received)

    def unexpected_local_polling(*_args, **_kwargs):
        raise AssertionError("Local Telegram polling must not start in bridge mode")

    monkeypatch.setattr("el_ai.main._run_cloudflare_bridge", bridge_runtime)
    monkeypatch.setattr("el_ai.main.TelegramBotAPI", unexpected_local_polling)

    asyncio.run(run(settings))

    assert called == [settings]



def test_main_loads_dotenv_without_overriding_process_environment(monkeypatch):
    calls = []
    monkeypatch.setattr("el_ai.main.load_dotenv", lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr("sys.argv", ["el-ai", "doctor"])
    monkeypatch.setattr("el_ai.doctor.main", lambda json_output: 0)

    try:
        main()
    except SystemExit as exc:
        assert exc.code == 0

    assert calls == [{"override": False}]



def test_task_plan_pause_and_resume_are_owner_scoped_and_resume_requires_confirmation(tmp_path):
    now = int(time.time())
    plans = TaskPlanStore(tmp_path / "task-plans.json")
    plan = plans.create(123, "demo.echo", ["prepare", "publish"], now=now)
    plans.mark_submitted(plan.plan_id, 123, 0, "12345678-1234-4234-8234-123456789abc")

    class FakeHub:
        pass

    hub = FakeHub()
    paused = asyncio.run(_owner_hub_command(
        f"/taskplanpause {plan.plan_id}",
        telegram_user_id=123,
        update_id=200,
        hub=hub,
        settings=_settings(enabled=True),
        task_plans=plans,
    ))
    assert "در حالت مکث" in paused
    assert plans.get_for_user(plan.plan_id, 123, now=now + 1).paused is True

    unconfirmed = asyncio.run(_owner_hub_command(
        f"/taskplanresume {plan.plan_id}",
        telegram_user_id=123,
        update_id=201,
        hub=hub,
        settings=_settings(enabled=True),
        task_plans=plans,
    ))
    assert "CONFIRM" in unconfirmed
    assert plans.get_for_user(plan.plan_id, 123, now=now + 2).paused is True

    foreign = asyncio.run(_owner_hub_command(
        f"/taskplanresume {plan.plan_id} CONFIRM",
        telegram_user_id=456,
        update_id=202,
        hub=hub,
        settings=_settings(enabled=True),
        task_plans=plans,
    ))
    assert "طرح پیدا نشد" in foreign

    resumed = asyncio.run(_owner_hub_command(
        f"/taskplanresume {plan.plan_id} CONFIRM",
        telegram_user_id=123,
        update_id=203,
        hub=hub,
        settings=_settings(enabled=True),
        task_plans=plans,
    ))
    assert "مکث طرح برداشته شد" in resumed
    assert plans.get_for_user(plan.plan_id, 123, now=now + 3).paused is False


def test_task_plan_cannot_be_paused_before_explicit_start(tmp_path):
    now = int(time.time())
    plans = TaskPlanStore(tmp_path / "task-plans.json")
    plan = plans.create(123, "demo.echo", ["prepare", "publish"], now=now)

    class FakeHub:
        pass

    rendered = asyncio.run(_owner_hub_command(
        f"/taskplanpause {plan.plan_id}",
        telegram_user_id=123,
        update_id=204,
        hub=FakeHub(),
        settings=_settings(enabled=True),
        task_plans=plans,
    ))
    assert "هنوز شروع نشده" in rendered
    assert plans.get_for_user(plan.plan_id, 123, now=now + 1).paused is False


def test_task_plan_pause_controls_are_owner_menu_only():
    commands = {item["command"] for item in OWNER_BOT_COMMANDS}
    assert {"taskplanpause", "taskplanresume"} <= commands
    assert {"/taskplanpause", "/taskplanresume"} <= OWNER_HUB_COMMANDS
