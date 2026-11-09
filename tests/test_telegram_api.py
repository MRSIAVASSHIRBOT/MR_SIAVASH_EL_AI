import httpx
import pytest

from el_ai.telegram.api import TelegramAPIError, TelegramBotAPI


@pytest.mark.asyncio
async def test_get_updates_and_send_message_use_bot_api():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.path, request.read()))
        if request.url.path.endswith("getUpdates"):
            return httpx.Response(200, json={"ok": True, "result": [{"update_id": 7}]})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 99}})

    api = TelegramBotAPI("123:abcdefghijklmnop", transport=httpx.MockTransport(handler))
    updates = await api.get_updates(offset=8, timeout=10)
    sent = await api.send_message(900, "hello")
    assert updates == [{"update_id": 7}]
    assert sent["message_id"] == 99
    assert calls[0][0].endswith("getUpdates")
    assert calls[1][0].endswith("sendMessage")
    await api.close()


@pytest.mark.asyncio
async def test_provider_error_does_not_leak_response_body_or_token():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="secret-token-invalid")

    api = TelegramBotAPI("123:abcdefghijklmnop", transport=httpx.MockTransport(handler))
    with pytest.raises(TelegramAPIError) as error:
        await api.call("getMe", {})
    assert "abcdefghijklmnop" not in str(error.value)
    assert "secret-token-invalid" not in str(error.value)
    await api.close()


def test_bot_token_is_required_and_malformed_values_rejected():
    with pytest.raises(ValueError):
        TelegramBotAPI("")
    with pytest.raises(ValueError):
        TelegramBotAPI("token/with/path")
