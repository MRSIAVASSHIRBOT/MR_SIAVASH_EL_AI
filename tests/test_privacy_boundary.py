import asyncio

from el_ai.config import Settings
from el_ai.main import run


def _update(update_id: int, user_id: int, text: str) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "from": {"id": user_id},
            "chat": {"id": user_id, "type": "private"},
            "text": text,
        },
    }


def test_owner_private_text_never_reaches_public_gateway(monkeypatch, tmp_path):
    site_queries: list[str] = []
    replies: list[tuple[int, str]] = []

    class FakeTelegramAPI:
        def __init__(self, _token: str, *, timeout_seconds: int):
            self.poll_count = 0

        async def set_my_commands(self, _commands, *, scope=None):
            return None

        async def get_updates(
            self,
            *,
            offset=None,
            timeout=25,
            include_business_updates=False,
        ):
            assert include_business_updates is False
            self.poll_count += 1
            if self.poll_count == 1:
                return [_update(1, 123, "private owner note")]
            if self.poll_count == 2:
                return [_update(2, 456, "public question")]
            raise asyncio.CancelledError

        async def send_message(self, chat_id: int, text: str):
            replies.append((chat_id, text))

        async def close(self):
            return None

    class FakeSiteGateway:
        def __init__(self, *_args, **_kwargs):
            return None

        async def ask(self, message: str):
            site_queries.append(message)
            return {"answer": "public answer"}

        def format_answer(self, result: dict[str, str]) -> str:
            return "PUBLIC: " + result["answer"]

        async def close(self):
            return None

    monkeypatch.setattr("el_ai.main.TelegramBotAPI", FakeTelegramAPI)
    monkeypatch.setattr("el_ai.main.SiteGatewayClient", FakeSiteGateway)
    monkeypatch.setattr("el_ai.main._build_hub_client", lambda *_args: None)
    monkeypatch.setattr(
        "el_ai.main.load_or_create_client_id",
        lambda _state_dir: "00000000-0000-4000-8000-000000000001",
    )

    settings = Settings(
        telegram_bot_token="123:test-token",
        owner_ids=frozenset({123}),
        public_enabled=True,
        state_dir=tmp_path,
        poll_timeout_seconds=1,
        request_timeout_seconds=5,
    )

    try:
        asyncio.run(run(settings))
    except asyncio.CancelledError:
        pass

    assert site_queries == ["public question"]
    assert replies == [
        (123, "پیامت دریافت شد. برای پرسش از دانش عمومی سایت از /site استفاده کن."),
        (456, "PUBLIC: public answer"),
    ]

def test_owner_hub_results_and_task_context_never_reach_public_gateway(monkeypatch, tmp_path):
    site_queries: list[str] = []
    replies: list[tuple[int, str]] = []
    task_id = "2f8e5d4c-3b2a-4910-8c7d-6e5f4a3b2c1d"

    class FakeTelegramAPI:
        def __init__(self, _token: str, *, timeout_seconds: int):
            self.poll_count = 0

        async def set_my_commands(self, _commands, *, scope=None):
            return None

        async def get_updates(
            self,
            *,
            offset=None,
            timeout=25,
            include_business_updates=False,
        ):
            assert include_business_updates is False
            self.poll_count += 1
            if self.poll_count == 1:
                return [_update(1, 123, f"/taskresult {task_id}")]
            if self.poll_count == 2:
                return [_update(2, 123, f"/taskprogress {task_id}")]
            if self.poll_count == 3:
                return [_update(3, 456, "public question")]
            raise asyncio.CancelledError

        async def send_message(self, chat_id: int, text: str):
            replies.append((chat_id, text))

        async def close(self):
            return None

    class FakeHubClient:
        async def get_task_result(self, telegram_user_id: int, requested_task_id: str):
            assert telegram_user_id == 123
            assert requested_task_id == task_id
            return {"result": "PRIVATE_HUB_RESULT_MARKER"}

        async def get_task(self, telegram_user_id: int, requested_task_id: str):
            assert telegram_user_id == 123
            assert requested_task_id == task_id
            return {"status": "succeeded", "task_type": "private_owner_task"}

        async def get_task_history(self, telegram_user_id: int, requested_task_id: str, *, limit: int):
            assert telegram_user_id == 123
            assert requested_task_id == task_id
            assert limit == 10
            return {
                "events": [{
                    "event_type": "task.succeeded",
                    "outcome": "succeeded",
                    "created_at": "2026-11-21T11:00:00+00:00",
                }],
                "has_more": False,
            }

        async def close(self):
            return None

    class FakeSiteGateway:
        def __init__(self, *_args, **_kwargs):
            return None

        async def ask(self, message: str):
            site_queries.append(message)
            return {"answer": "public answer"}

        def format_answer(self, result: dict[str, str]) -> str:
            return "PUBLIC: " + result["answer"]

        async def close(self):
            return None

    monkeypatch.setattr("el_ai.main.TelegramBotAPI", FakeTelegramAPI)
    monkeypatch.setattr("el_ai.main.SiteGatewayClient", FakeSiteGateway)
    monkeypatch.setattr("el_ai.main._build_hub_client", lambda *_args: FakeHubClient())
    monkeypatch.setattr(
        "el_ai.main.load_or_create_client_id",
        lambda _state_dir: "00000000-0000-4000-8000-000000000001",
    )

    settings = Settings(
        telegram_bot_token="123:test-token",
        owner_ids=frozenset({123}),
        public_enabled=True,
        hub_integration_enabled=True,
        hub_api_base_url="http://127.0.0.1:8000",
        state_dir=tmp_path,
        poll_timeout_seconds=1,
        request_timeout_seconds=5,
    )

    try:
        asyncio.run(run(settings))
    except asyncio.CancelledError:
        pass

    assert site_queries == ["public question"]
    assert len(replies) == 3
    assert replies[0][0] == 123
    assert "PRIVATE_HUB_RESULT_MARKER" in replies[0][1]
    assert replies[1][0] == 123
    assert "private_owner_task" in replies[1][1]
    assert replies[2] == (456, "PUBLIC: public answer")
    assert all("PRIVATE_HUB_RESULT_MARKER" not in text for chat_id, text in replies if chat_id == 456)
    assert all("private_owner_task" not in text for chat_id, text in replies if chat_id == 456)
