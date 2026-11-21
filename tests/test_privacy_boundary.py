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

        async def get_updates(self, *, offset=None, timeout=25):
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
