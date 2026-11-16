from datetime import UTC, datetime, timedelta

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


def _store():
    store = MemoryStore()
    store.save("render-client", {
        "access_token": "render-access",
        "access_expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        "refresh_token": "render-refresh",
        "refresh_expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        "scopes": ["video:read", "video:render", "video:cancel"],
    })
    return store


@pytest.mark.asyncio
async def test_video_render_client_uses_scoped_routes_and_idempotency_header():
    analysis_id = "12345678-1234-4234-8234-123456789abc"
    draft_id = "22345678-1234-4234-8234-123456789abc"
    render_id = "32345678-1234-4234-8234-123456789abc"
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.endswith("/video/render-drafts"):
            return httpx.Response(200, json={
                "analysis_id": analysis_id,
                "drafts": [{"draft_id": draft_id, "renderer_ready": True}],
                "count": 1,
            })
        if request.method == "POST" and request.url.path.endswith("/video/renders"):
            return httpx.Response(202, json={
                "render_id": render_id,
                "status": "accepted",
                "render_kind": "deterministic_ffmpeg",
                "created": True,
            })
        if request.method == "DELETE":
            return httpx.Response(200, json={
                "render_id": render_id,
                "status": "running",
                "cancellation_requested": True,
            })
        return httpx.Response(200, json={
            "render_id": render_id,
            "status": "completed",
            "output_available": True,
        })

    client = HubClient(
        "http://127.0.0.1:8000",
        "render-client",
        _store(),
        transport=httpx.MockTransport(handler),
    )
    drafts = await client.list_video_render_drafts(123, analysis_id)
    submitted = await client.submit_video_render(
        123, draft_id, idempotency_key="el-ai:123:42:video-render"
    )
    status = await client.get_video_render(123, render_id)
    cancelled = await client.cancel_video_render(123, render_id)

    assert drafts["count"] == 1
    assert submitted["render_kind"] == "deterministic_ffmpeg"
    assert status["output_available"] is True
    assert cancelled["cancellation_requested"] is True
    assert calls[0].url.path == "/integrations/telegram/video/render-drafts"
    assert calls[0].url.params["analysis_id"] == analysis_id
    assert calls[1].method == "POST"
    assert calls[1].url.path == "/integrations/telegram/video/renders"
    assert calls[1].headers["Idempotency-Key"] == "el-ai:123:42:video-render"
    assert calls[1].read().decode("utf-8") == '{"draft_id":"' + draft_id + '"}'
    assert calls[2].url.path.endswith("/" + render_id)
    assert calls[3].method == "DELETE"
    assert HubClient._route_template(calls[2].url.path) == (
        "/integrations/telegram/video/renders/{render_id}"
    )
    assert all(call.headers["Authorization"] == "Bearer render-access" for call in calls)
    await client.close()


@pytest.mark.asyncio
async def test_video_render_client_rejects_invalid_ids_and_idempotency_keys():
    client = HubClient("http://127.0.0.1:8000", "render-invalid", _store())
    with pytest.raises(ValueError):
        await client.list_video_render_drafts(123, "not-a-uuid")
    with pytest.raises(ValueError):
        await client.submit_video_render(
            123, "not-a-uuid", idempotency_key="valid-key"
        )
    with pytest.raises(ValueError, match="idempotency"):
        await client.submit_video_render(
            123, "22345678-1234-4234-8234-123456789abc", idempotency_key=""
        )
    with pytest.raises(ValueError):
        await client.get_video_render(123, "not-a-uuid")
    with pytest.raises(ValueError):
        await client.cancel_video_render(123, "not-a-uuid")
    await client.close()
