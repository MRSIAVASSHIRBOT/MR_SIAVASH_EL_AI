import asyncio

from el_ai.config import Settings
from el_ai.integrations.hub import HubClientError
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


def test_scene_commands_are_owner_only_and_documented():
    names = {item["command"] for item in OWNER_BOT_COMMANDS}
    assert {"videoscenes", "videoscenestatus", "videoscenecancel", "videoscenehistory", "videosceneexport"} <= names
    assert {"/videoscenes", "/videoscenestatus", "/videoscenecancel", "/videoscenehistory", "/videosceneexport"} <= OWNER_HUB_COMMANDS

    router = MessageRouter(
        AccessController(frozenset({123}), public_enabled=False, rate_limit=20),
        client_id="client-id",
    )
    owner_help = router.route({
        "from": {"id": 123},
        "chat": {"type": "private"},
        "text": "/help",
    })
    public_help = router.route({
        "from": {"id": 456},
        "chat": {"type": "private"},
        "text": "/help",
    })
    assert "/videoscenes ASSET_ID" in owner_help
    assert "/videoscenestatus JOB_ID" in owner_help
    assert "/videoscenecancel JOB_ID" in owner_help
    assert "/videoscenehistory [CURSOR]" in owner_help
    assert "/videosceneexport JOB_ID" in owner_help
    assert not public_help or "/videoscenes" not in public_help


def test_scene_submission_validates_bounds_and_uses_update_idempotency():
    asset_id = "22345678-1234-4234-8234-123456789abc"
    calls = []

    class FakeHub:
        async def submit_video_scene_job(
            self, telegram_user_id, requested_asset_id, *,
            threshold, max_scene_changes, idempotency_key,
        ):
            calls.append((
                telegram_user_id, requested_asset_id, threshold,
                max_scene_changes, idempotency_key,
            ))
            return {"job_id": "32345678-1234-4234-8234-123456789abc", "status": "queued"}

    result = asyncio.run(_owner_hub_command(
        f"/videoscenes {asset_id} 0.4 12",
        telegram_user_id=123,
        update_id=77,
        hub=FakeHub(),
        settings=_settings(),
    ))
    assert "queued" in result
    assert calls == [(
        123, asset_id, 0.4, 12, "el-ai:123:77:video-scene",
    )]

    invalid = asyncio.run(_owner_hub_command(
        f"/videoscenes {asset_id} 1.2 12",
        telegram_user_id=123,
        update_id=78,
        hub=FakeHub(),
        settings=_settings(),
    ))
    assert "بین 0.05 و 0.95" in invalid
    assert len(calls) == 1


def test_scene_status_and_cancel_use_owner_scoped_hub_methods():
    job_id = "32345678-1234-4234-8234-123456789abc"
    calls = []

    class FakeHub:
        async def get_video_scene_job(self, telegram_user_id, requested_job_id):
            calls.append(("get", telegram_user_id, requested_job_id))
            return {"job_id": requested_job_id, "status": "succeeded"}

        async def cancel_video_scene_job(self, telegram_user_id, requested_job_id):
            calls.append(("cancel", telegram_user_id, requested_job_id))
            return {"job_id": requested_job_id, "status": "cancelled"}

    status = asyncio.run(_owner_hub_command(
        f"/videoscenestatus {job_id}", telegram_user_id=123, update_id=79,
        hub=FakeHub(), settings=_settings(),
    ))
    cancelled = asyncio.run(_owner_hub_command(
        f"/videoscenecancel {job_id}", telegram_user_id=123, update_id=80,
        hub=FakeHub(), settings=_settings(),
    ))
    assert "succeeded" in status
    assert "cancelled" in cancelled
    assert calls == [
        ("get", 123, job_id),
        ("cancel", 123, job_id),
    ]


def test_scene_submission_explains_missing_explicit_scope():
    class FakeHub:
        async def submit_video_scene_job(self, *_args, **_kwargs):
            raise HubClientError("scope missing", status_code=403)

    result = asyncio.run(_owner_hub_command(
        "/videoscenes 22345678-1234-4234-8234-123456789abc",
        telegram_user_id=123,
        update_id=81,
        hub=FakeHub(),
        settings=_settings(),
    ))
    assert "video:analyze" in result
    assert "صریحاً" in result


def test_scene_history_and_csv_export_are_owner_commands():
    job_id = "32345678-1234-4234-8234-123456789abc"
    calls = []

    class FakeHub:
        async def list_video_scene_jobs(self, telegram_user_id, *, limit, cursor):
            calls.append(("history", telegram_user_id, limit, cursor))
            return {
                "items": [{"job_id": job_id, "status": "succeeded"}],
                "count": 1,
                "has_more": True,
                "next_cursor": "next-page-token",
            }

        async def get_video_scene_job(self, telegram_user_id, requested_job_id):
            calls.append(("export", telegram_user_id, requested_job_id))
            return {
                "job_id": requested_job_id,
                "status": "succeeded",
                "result": {"scene_start_times": [0.0, 1.25, 4.5]},
            }

    history = asyncio.run(_owner_hub_command(
        "/videoscenehistory page-token",
        telegram_user_id=123, update_id=82, hub=FakeHub(), settings=_settings(),
    ))
    exported = asyncio.run(_owner_hub_command(
        f"/videosceneexport {job_id}",
        telegram_user_id=123, update_id=83, hub=FakeHub(), settings=_settings(),
    ))
    assert "next-page-token" in history
    assert "scene_index,start_seconds" in exported
    assert "2,1.250" in exported
    assert "3,4.500" in exported
    assert calls == [
        ("history", 123, 10, "page-token"),
        ("export", 123, job_id),
    ]



def test_scene_csv_export_sends_a_document_when_telegram_context_is_available():
    job_id = "32345678-1234-4234-8234-123456789abc"
    calls = []

    class FakeHub:
        async def get_video_scene_job(self, telegram_user_id, requested_job_id):
            assert telegram_user_id == 123
            assert requested_job_id == job_id
            return {
                "job_id": requested_job_id,
                "status": "succeeded",
                "result": {"scene_start_times": [0.0, 1.25, 4.5]},
            }

    class FakeAPI:
        async def send_document(self, chat_id, document, *, filename, caption):
            calls.append((chat_id, document, filename, caption))

    response = asyncio.run(_owner_hub_command(
        f"/videosceneexport {job_id}",
        telegram_user_id=123,
        update_id=84,
        hub=FakeHub(),
        settings=_settings(),
        api=FakeAPI(),
        chat_id=900,
    ))

    assert response == ""
    assert len(calls) == 1
    chat_id, document, filename, caption = calls[0]
    assert chat_id == 900
    assert filename == "scene-times-32345678.csv"
    assert caption.endswith("32345678")
    assert document.decode("utf-8-sig").splitlines() == [
        "scene_index,start_seconds",
        "1,0.000",
        "2,1.250",
        "3,4.500",
    ]
