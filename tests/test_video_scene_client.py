from datetime import UTC, datetime, timedelta
import json

import httpx
import pytest

from el_ai.integrations.hub import HubClient


class MemoryStore:
    def __init__(self):
        self.items = {}

    def save(self, client_id, credentials):
        self.items[client_id] = dict(credentials)

    def load(self, client_id):
        value = self.items.get(client_id)
        return dict(value) if value is not None else None

    def delete(self, client_id):
        self.items.pop(client_id, None)


def _store(client_id="scene-client"):
    store = MemoryStore()
    store.save(client_id, {
        "access_token": "scene-access",
        "access_expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        "refresh_token": "scene-refresh",
        "refresh_expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        "scopes": ["video:analyze", "video:read", "video:cancel"],
    })
    return store


@pytest.mark.asyncio
async def test_scene_job_client_uses_scoped_routes_and_idempotency_header():
    asset_id = "22345678-1234-4234-8234-123456789abc"
    job_id = "32345678-1234-4234-8234-123456789abc"
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.method == "POST" and request.url.path == "/integrations/telegram/video/scene-jobs":
            return httpx.Response(202, json={
                "job_id": job_id, "status": "queued", "created": True,
            })
        if request.method == "POST" and request.url.path.endswith("/cancel"):
            return httpx.Response(200, json={"job_id": job_id, "status": "cancelled"})
        return httpx.Response(200, json={
            "job_id": job_id,
            "status": "succeeded",
            "result": {"scene_start_times": [0.0, 2.0], "scene_count": 2},
        })

    client = HubClient(
        "http://127.0.0.1:8000",
        "scene-client",
        _store(),
        transport=httpx.MockTransport(handler),
    )
    accepted = await client.submit_video_scene_job(
        123, asset_id, threshold=0.4, max_scene_changes=12,
        idempotency_key="el-ai:123:77:video-scene",
    )
    status = await client.get_video_scene_job(123, job_id)
    cancelled = await client.cancel_video_scene_job(123, job_id)

    assert accepted["status"] == "queued"
    assert status["result"]["scene_start_times"] == [0.0, 2.0]
    assert cancelled["status"] == "cancelled"
    assert calls[0].headers["Idempotency-Key"] == "el-ai:123:77:video-scene"
    assert json.loads(calls[0].content.decode("utf-8")) == {
        "asset_id": asset_id, "threshold": 0.4, "max_scene_changes": 12,
    }
    assert HubClient._route_template(calls[0].url.path) == "/integrations/telegram/video/scene-jobs"
    assert HubClient._route_template(calls[1].url.path) == "/integrations/telegram/video/scene-jobs/{job_id}"
    assert HubClient._route_template(calls[2].url.path) == "/integrations/telegram/video/scene-jobs/{job_id}/cancel"
    assert all(call.headers["Authorization"] == "Bearer scene-access" for call in calls)
    await client.close()


@pytest.mark.asyncio
async def test_scene_job_client_rejects_invalid_ids_bounds_and_keys():
    client = HubClient("http://127.0.0.1:8000", "scene-invalid", _store("scene-invalid"))
    with pytest.raises(ValueError):
        await client.submit_video_scene_job(
            123, "not-a-uuid", idempotency_key="valid-key",
        )
    with pytest.raises(ValueError):
        await client.submit_video_scene_job(
            123, "22345678-1234-4234-8234-123456789abc",
            threshold=1.1, idempotency_key="valid-key",
        )
    with pytest.raises(ValueError):
        await client.submit_video_scene_job(
            123, "22345678-1234-4234-8234-123456789abc",
            max_scene_changes=25, idempotency_key="valid-key",
        )
    with pytest.raises(ValueError):
        await client.submit_video_scene_job(
            123, "22345678-1234-4234-8234-123456789abc",
            idempotency_key="bad\nkey",
        )
    with pytest.raises(ValueError):
        await client.get_video_scene_job(123, "not-a-uuid")
    with pytest.raises(ValueError):
        await client.cancel_video_scene_job(123, "not-a-uuid")
    await client.close()
