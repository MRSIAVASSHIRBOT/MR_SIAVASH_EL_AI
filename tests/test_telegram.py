import httpx
import pytest

from el_ai.telegram import TelegramAPIError, TelegramClient

TOKEN = "123456:abcdefghijklmnopqrstuvwxyz123456"


@pytest.mark.asyncio
async def test_get_updates_uses_only_message_updates_and_supplied_offset():
    def handler(request):
        assert request.url.path.endswith("/getUpdates")
        assert request.url.path.startswith("/bot" + TOKEN + "/")
        assert request.read()
        return httpx.Response(200, json={"ok": True, "result": []})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = TelegramClient(TOKEN, client=http)
    try:
        result = await client.get_updates(offset=42, timeout=20)
    finally:
        await http.aclose()

    assert result == []


@pytest.mark.asyncio
async def test_api_error_does_not_expose_token_or_provider_description():
    def handler(request):
        return httpx.Response(
            401,
            json={"ok": False, "error_code": 401, "description": "invalid token " + TOKEN},
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = TelegramClient(TOKEN, client=http)
    try:
        with pytest.raises(TelegramAPIError) as captured:
            await client.get_updates(offset=None, timeout=10)
    finally:
        await http.aclose()

    assert TOKEN not in str(captured.value)
    assert "invalid token" not in str(captured.value)


@pytest.mark.asyncio
async def test_send_message_posts_bounded_plain_text():
    seen = {}

    def handler(request):
        seen["payload"] = request.content.decode("utf-8")
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 7}})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = TelegramClient(TOKEN, client=http)
    try:
        result = await client.send_message(123, "hello")
    finally:
        await http.aclose()

    assert result == {"message_id": 7}
    assert '"chat_id":123' in seen["payload"]
    assert '"text":"hello"' in seen["payload"]


@pytest.mark.asyncio
async def test_send_message_rejects_oversized_text_before_network_call():
    client = TelegramClient(TOKEN)
    with pytest.raises(ValueError):
        await client.send_message(123, "x" * 4097)
    await client.aclose()
