from el_ai.application import TelegramApplication


class FakeTelegramClient:
    def __init__(self):
        self.messages = []

    async def send_message(self, chat_id, text):
        self.messages.append((chat_id, text))
        return {"message_id": len(self.messages)}


def update(user_id=123, chat_id=123, text="/status", chat_type="private"):
    return {
        "update_id": 1,
        "message": {
            "chat": {"id": chat_id, "type": chat_type},
            "from": {"id": user_id},
            "text": text,
        },
    }


async def test_owner_receives_explicit_not_configured_agent_status():
    client = FakeTelegramClient()
    app = TelegramApplication(client, owner_ids={123})

    await app.handle_update(update())

    assert len(client.messages) == 1
    assert client.messages[0][0] == 123
    assert "agent=not_configured" in client.messages[0][1]
    assert "execution=disabled" in client.messages[0][1]


async def test_unknown_user_is_silent_when_public_mode_is_disabled():
    client = FakeTelegramClient()
    app = TelegramApplication(client, owner_ids={123})

    await app.handle_update(update(user_id=456, chat_id=456, text="/start"))

    assert client.messages == []


async def test_group_messages_are_ignored_even_for_owner():
    client = FakeTelegramClient()
    app = TelegramApplication(client, owner_ids={123})

    await app.handle_update(update(chat_type="group"))

    assert client.messages == []


async def test_public_mode_exposes_only_limited_help_and_not_owner_identity():
    client = FakeTelegramClient()
    app = TelegramApplication(client, owner_ids={123}, public_enabled=True)

    await app.handle_update(update(user_id=456, chat_id=456, text="/help"))
    await app.handle_update(update(user_id=456, chat_id=456, text="/whoami"))

    assert len(client.messages) == 2
    assert "/status" in client.messages[0][1]
    assert "فقط برای مالک" in client.messages[1][1]
