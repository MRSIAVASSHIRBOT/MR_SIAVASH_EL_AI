import json

import httpx
import pytest

from el_ai.telegram.api import TelegramAPIError, TelegramBotAPI, split_telegram_message


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
    get_updates_payload = json.loads(calls[0][1])
    assert get_updates_payload["allowed_updates"] == ["message"]
    assert "business_message" not in get_updates_payload["allowed_updates"]
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
    assert error.value.__context__ is None
    await api.close()


@pytest.mark.asyncio
async def test_send_message_enforces_telegram_utf16_limit():
    api = TelegramBotAPI("123:abcdefghijklmnop")
    with pytest.raises(ValueError, match="UTF-16"):
        await api.send_message(900, "😀" * 2049)
    await api.close()


@pytest.mark.asyncio
async def test_send_message_rejects_unpaired_surrogates_before_network_io():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={"ok": True, "result": True})

    api = TelegramBotAPI(
        "123:abcdefghijklmnop",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ValueError, match="invalid Unicode"):
        await api.send_message(900, "bad " + chr(0xD800))
    assert calls == []
    await api.close()


def test_split_telegram_message_rejects_unpaired_surrogates():
    with pytest.raises(ValueError, match="invalid Unicode"):
        split_telegram_message("bad " + chr(0xD800))


@pytest.mark.asyncio
async def test_send_video_rejects_invalid_unicode_caption_before_network_io():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={"ok": True, "result": True})

    api = TelegramBotAPI(
        "123:abcdefghijklmnop",
        transport=httpx.MockTransport(handler),
    )
    video = bytes((0, 0, 0, 24)) + b"ftypisom"
    with pytest.raises(ValueError, match="invalid Unicode"):
        await api.send_video(
            900,
            video,
            filename="video.mp4",
            caption=chr(0xD800),
        )
    assert calls == []
    await api.close()


def test_split_telegram_message_preserves_text_and_respects_utf16_limit():
    text = "پاسخ ✨ 😀 " * 1200

    chunks = split_telegram_message(text)

    assert len(chunks) > 1
    assert "".join(chunks) == text
    assert all(len(chunk.encode("utf-16-le")) // 2 <= 4096 for chunk in chunks)


def test_split_telegram_message_prefers_whitespace_boundaries():
    text = "alpha " * 1000

    chunks = split_telegram_message(text)

    assert len(chunks) > 1
    assert all(chunk.endswith(" ") for chunk in chunks[:-1])
    assert "".join(chunks) == text


def test_split_telegram_message_rejects_invalid_limits():
    with pytest.raises(ValueError):
        split_telegram_message("hello", max_units=0)
    with pytest.raises(ValueError):
        split_telegram_message("hello", max_units=True)


def test_split_telegram_message_handles_unbroken_astral_text():
    text = "😀" * 5000

    chunks = split_telegram_message(text)

    assert len(chunks) > 1
    assert "".join(chunks) == text
    assert all(len(chunk.encode("utf-16-le")) // 2 <= 4096 for chunk in chunks)


@pytest.mark.asyncio
async def test_set_my_commands_sends_scoped_owner_menu_without_leaking_credentials():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.path, request.read()))
        return httpx.Response(200, json={"ok": True, "result": True})

    api = TelegramBotAPI("123:abcdefghijklmnop", transport=httpx.MockTransport(handler))
    commands = [
        {"command": "start", "description": "شروع گفتگو"},
        {"command": "videohistory", "description": "تاریخچه تولید ویدئو"},
    ]
    result = await api.set_my_commands(
        commands,
        scope={"type": "chat", "chat_id": 123456},
    )

    assert result is True
    assert calls[0][0].endswith("setMyCommands")
    import json
    payload = json.loads(calls[0][1])
    assert payload["commands"] == commands
    assert payload["scope"] == {"type": "chat", "chat_id": 123456}
    assert "abcdefghijklmnop" not in repr(payload)
    await api.close()


@pytest.mark.asyncio
async def test_set_my_commands_rejects_invalid_entries_and_unsafe_scopes():
    api = TelegramBotAPI("123:abcdefghijklmnop")
    with pytest.raises(ValueError):
        await api.set_my_commands([{"command": "Bad", "description": "invalid"}])
    with pytest.raises(ValueError):
        await api.set_my_commands([
            {"command": "help", "description": "one"},
            {"command": "help", "description": "duplicate"},
        ])
    with pytest.raises(ValueError):
        await api.set_my_commands(
            [{"command": "help", "description": "help"}],
            scope={"type": "chat", "chat_id": True},
        )
    await api.close()


@pytest.mark.asyncio
async def test_get_updates_rejects_malformed_or_oversized_collections():
    for result in ([{"update_id": 1}, None], [{"update_id": index} for index in range(101)]):
        def handler(_request: httpx.Request, payload=result) -> httpx.Response:
            return httpx.Response(200, json={"ok": True, "result": payload})

        api = TelegramBotAPI(
            "123:abcdefghijklmnop",
            transport=httpx.MockTransport(handler),
        )
        with pytest.raises(TelegramAPIError, match="invalid update collection"):
            await api.get_updates(offset=None, timeout=10)
        await api.close()

@pytest.mark.asyncio
async def test_send_video_uses_bounded_multipart_upload_and_sanitizes_failures():
    mp4 = b"\x00\x00\x00\x18ftypisom"
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read()
        calls.append((request, body))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 88}})

    api = TelegramBotAPI(
        "123:abcdefghijklmnop",
        transport=httpx.MockTransport(handler),
    )
    result = await api.send_video(
            900, video,
        mp4,
        filename="render-123.mp4",
        caption="رندر محلی",
    )
    assert result["message_id"] == 88
    request, body = calls[0]
    assert request.url.path.endswith("sendVideo")
    assert request.headers["content-type"].startswith("multipart/form-data; boundary=")
    assert b'name="chat_id"' in body and b"900" in body
    assert b'name="supports_streaming"' in body
    assert b'filename="render-123.mp4"' in body
    assert b"Content-Type: video/mp4" in body
    assert mp4 in body
    await api.close()


@pytest.mark.asyncio
async def test_send_video_rejects_oversized_invalid_or_unsafe_uploads():
    api = TelegramBotAPI("123:abcdefghijklmnop")
    mp4 = b"\x00\x00\x00\x18ftypisom"
    with pytest.raises(ValueError, match="MP4"):
        await api.send_video(900, b"not-a-video!!", filename="render.mp4")
    with pytest.raises(ValueError, match="filename"):
        await api.send_video(900, mp4, filename="../render.mp4")
    with pytest.raises(ValueError, match="chat ID"):
        await api.send_video(True, mp4, filename="render.mp4")
    with pytest.raises(ValueError, match="caption"):
        await api.send_video(900, mp4, filename="render.mp4", caption="😀" * 513)
    await api.close()



@pytest.mark.asyncio
async def test_send_document_uses_bounded_multipart_csv_upload():
    calls = []
    content = b"\xef\xbb\xbfscene_index,start_seconds\n1,0.000\n"

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read()
        calls.append((request, body))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 91}})

    api = TelegramBotAPI(
        "123:abcdefghijklmnop",
        transport=httpx.MockTransport(handler),
    )
    result = await api.send_document(
        900,
        content,
        filename="scene-times-12345678.csv",
        caption="زمان‌های شروع صحنه",
    )

    assert result["message_id"] == 91
    request, body = calls[0]
    assert request.url.path.endswith("sendDocument")
    assert request.headers["content-type"].startswith("multipart/form-data; boundary=")
    assert b'name="chat_id"' in body and b"900" in body
    assert b'filename="scene-times-12345678.csv"' in body
    assert b"Content-Type: text/csv" in body
    assert content in body
    await api.close()


@pytest.mark.asyncio
async def test_send_document_rejects_invalid_or_unsafe_uploads():
    api = TelegramBotAPI("123:abcdefghijklmnop")
    with pytest.raises(ValueError, match="chat ID"):
        await api.send_document(True, b"csv", filename="scene.csv")
    with pytest.raises(ValueError, match="non-empty"):
        await api.send_document(900, b"", filename="scene.csv")
    with pytest.raises(ValueError, match="filename"):
        await api.send_document(900, b"csv", filename="../scene.csv")
    with pytest.raises(ValueError, match="caption"):
        await api.send_document(900, b"csv", filename="scene.csv", caption="😀" * 513)
    await api.close()
