import asyncio
import json

from el_ai.config import Settings
from el_ai.main import OWNER_BOT_COMMANDS, OWNER_HUB_COMMANDS, _owner_hub_command
from el_ai.telegram.access import AccessController
from el_ai.telegram.router import MessageRouter


def _settings():
    return Settings(
        telegram_bot_token="test-token",
        owner_ids=frozenset({123}),
        hub_api_base_url="http://127.0.0.1:8000",
        hub_integration_enabled=True,
    )


def test_video_render_commands_are_owner_only_and_in_the_private_menu():
    names = {item["command"] for item in OWNER_BOT_COMMANDS}
    assert {"videodrafts", "videorenders", "videorender", "videorenderstatus", "videorenderdownload", "videorendercancel"} <= names
    assert {
        "/videodrafts", "/videorenders", "/videorender", "/videorenderstatus", "/videorenderdownload", "/videorendercancel"
    } <= OWNER_HUB_COMMANDS
    router = MessageRouter(
        AccessController(frozenset({123}), public_enabled=False, rate_limit=20),
        client_id="client-id",
    )
    help_text = router.route({
        "from": {"id": 123},
        "chat": {"type": "private"},
        "text": "/help",
    })
    assert "/videorender DRAFT_ID" in help_text
    assert "/videorenderstatus RENDER_ID" in help_text
    assert "/videorenders [CURSOR]" in help_text
    assert "/videorenderdownload RENDER_ID" in help_text


def test_video_render_commands_validate_ids_and_submit_with_update_idempotency():
    calls = []
    draft_id = "22345678-1234-4234-8234-123456789abc"
    render_id = "32345678-1234-4234-8234-123456789abc"
    analysis_id = "12345678-1234-4234-8234-123456789abc"

    class FakeHub:
        async def list_video_render_drafts(self, telegram_user_id, requested_analysis_id):
            calls.append(("drafts", telegram_user_id, requested_analysis_id))
            return {"analysis_id": requested_analysis_id, "drafts": [], "count": 0}

        async def submit_video_render(self, telegram_user_id, requested_draft_id, *, idempotency_key):
            calls.append(("submit", telegram_user_id, requested_draft_id, idempotency_key))
            return {
                "render_id": render_id,
                "status": "accepted",
                "created": True,
                "render_kind": "deterministic_ffmpeg",
            }

        async def get_video_render(self, telegram_user_id, requested_render_id):
            calls.append(("status", telegram_user_id, requested_render_id))
            return {
                "render_id": requested_render_id,
                "status": "completed",
                "output_available": True,
            }

        async def list_video_renders(self, telegram_user_id, *, limit=10, cursor=None):
            calls.append(("list", telegram_user_id, limit, cursor))
            return {"renders": [{"render_id": render_id, "status": "completed"}], "count": 1, "next_cursor": "next"}

        async def download_video_render(self, telegram_user_id, requested_render_id):
            calls.append(("download", telegram_user_id, requested_render_id))
            return b"\\x00\\x00\\x00\\x18ftypisom"

        async def cancel_video_render(self, telegram_user_id, requested_render_id):
            calls.append(("cancel", telegram_user_id, requested_render_id))
            return {
                "render_id": requested_render_id,
                "status": "running",
                "cancellation_requested": True,
            }

    hub = FakeHub()
    settings = _settings()
    drafts = asyncio.run(_owner_hub_command(
        f"/videodrafts {analysis_id}", telegram_user_id=123, update_id=80,
        hub=hub, settings=settings,
    ))
    payload = json.loads(drafts)
    assert payload["analysis_id"] == analysis_id
    assert payload["render_kind"] == "deterministic_ffmpeg"

    submitted = asyncio.run(_owner_hub_command(
        f"/videorender {draft_id}", telegram_user_id=123, update_id=81,
        hub=hub, settings=settings,
    ))
    assert json.loads(submitted)["render_id"] == render_id
    assert calls[1] == (
        "submit", 123, draft_id, "el-ai:123:81:video-render"
    )

    status = asyncio.run(_owner_hub_command(
        f"/videorenderstatus {render_id}", telegram_user_id=123, update_id=82,
        hub=hub, settings=settings,
    ))
    assert "داشبورد Hub" in status

    cancelled = asyncio.run(_owner_hub_command(
        f"/videorendercancel {render_id}", telegram_user_id=123, update_id=83,
        hub=hub, settings=settings,
    ))
    assert json.loads(cancelled)["cancellation_requested"] is True


    history = asyncio.run(_owner_hub_command(
        "/videorenders next_cursor", telegram_user_id=123, update_id=84,
        hub=hub, settings=settings,
    ))
    history_payload = json.loads(history)
    assert history_payload["renders"][0]["render_id"] == render_id
    assert calls[-1] == ("list", 123, 10, "next_cursor")

    sent = []
    class FakeTelegramAPI:
        async def send_video(self, chat_id, video, *, filename, caption=None):
            sent.append((chat_id, video, filename, caption))
            return {"message_id": 99}

    delivered = asyncio.run(_owner_hub_command(
        f"/videorenderdownload {render_id}", telegram_user_id=123, update_id=85,
        hub=hub, settings=settings, api=FakeTelegramAPI(), chat_id=900,
    ))
    assert "ارسال شد" in delivered
    assert sent[0][0] == 900
    assert sent[0][1] == b"\\x00\\x00\\x00\\x18ftypisom"
    assert sent[0][2] == f"render-{render_id}.mp4"

    invalid = asyncio.run(_owner_hub_command(
        "/videorender invalid", telegram_user_id=123, update_id=84,
        hub=hub, settings=settings,
    ))
    assert "UUID" in invalid


def test_video_render_submission_explains_unprepared_drafts():
    from el_ai.integrations.hub import HubClientError

    class FakeHub:
        async def submit_video_render(self, telegram_user_id, draft_id, *, idempotency_key):
            raise HubClientError("conflict", status_code=409)

    result = asyncio.run(_owner_hub_command(
        "/videorender 22345678-1234-4234-8234-123456789abc",
        telegram_user_id=123,
        update_id=90,
        hub=FakeHub(),
        settings=_settings(),
    ))
    assert "Timeline" in result
    assert "داشبورد Hub" in result
