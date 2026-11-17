import asyncio
import json

from el_ai.config import Settings
from el_ai.integrations.hub import HubClientError
from el_ai.integrations.secrets import SecretStoreError
from el_ai.main import (
    main,
    _build_hub_client,
    _format_result,
    _owner_hub_command,
    _runtime_health_response,
    _register_command_menu,
    OWNER_HUB_COMMANDS,
    OWNER_BOT_COMMANDS,
    _poll_task_watches,
    _send_response,
)
from el_ai.observability import RuntimeMetrics
from el_ai.telegram.task_watch import TaskWatchStore
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
