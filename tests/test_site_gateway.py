import json
import time

import httpx
import pytest

from el_ai.integrations.site_gateway import SiteGatewayClient, SiteGatewayError


@pytest.mark.asyncio
async def test_public_gateway_sends_only_message_and_validates_source_links():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["payload"] = json.loads(request.read())
        return httpx.Response(
            200,
            json={
                "answer": "این پاسخ از دانش عمومی سایت است.",
                "mode": "knowledge_fallback",
                "handoff": False,
                "sources": [
                    {"title": "معرفی سایت", "href": "./index.html#home"},
                    {"title": "خارجی", "href": "https://evil.example/steal"},
                    {"title": "شبکه", "href": "//evil.example/redirect"},
                    {"title": "Traversal", "href": "./../private"},
                ],
            },
        )

    client = SiteGatewayClient(
        "https://gateway.example",
        "https://example.github.io/site",
        transport=httpx.MockTransport(handler),
    )
    result = await client.ask("معرفی سایت")
    assert captured["path"] == "/v1/assistant/chat"
    assert captured["payload"] == {"message": "معرفی سایت"}
    assert result.mode == "knowledge_fallback"
    assert len(result.sources) == 1
    assert result.sources[0].url == "https://example.github.io/site/index.html#home"
    rendered = client.format_answer(result)
    assert "بدون استنتاج مدل زبانی" in rendered
    assert "evil.example" not in rendered
    await client.close()


@pytest.mark.asyncio
async def test_public_gateway_rejects_provider_errors_without_leaking_body():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="internal-provider-secret")

    client = SiteGatewayClient(
        "https://gateway.example",
        "https://example.github.io/site",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(SiteGatewayError) as error:
        await client.ask("hello")
    assert "internal-provider-secret" not in str(error.value)
    await client.close()


@pytest.mark.asyncio
async def test_public_gateway_rate_limit_is_bounded():
    client = SiteGatewayClient(
        "https://gateway.example",
        "https://example.github.io/site",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"answer": "ok"})
        ),
    )
    client._request_times = [time.monotonic()] * client.MAX_REQUESTS_PER_MINUTE
    with pytest.raises(SiteGatewayError, match="limit"):
        await client.ask("hello")
    await client.close()


def test_public_gateway_requires_https_for_remote_urls():
    with pytest.raises(ValueError, match="HTTPS"):
        SiteGatewayClient("http://gateway.example", "https://example.github.io/site")
    with pytest.raises(ValueError):
        SiteGatewayClient(
            "https://user:password@gateway.example",
            "https://example.github.io/site",
        )



@pytest.mark.asyncio
async def test_public_gateway_rejects_oversized_user_message_without_network_call():
    client = SiteGatewayClient(
        "https://gateway.example",
        "https://example.github.io/site",
        transport=httpx.MockTransport(
            lambda _request: pytest.fail("Oversized message must not reach the Gateway")
        ),
    )
    with pytest.raises(ValueError, match="1200"):
        await client.ask("x" * 1201)
    await client.close()



@pytest.mark.asyncio
async def test_public_gateway_streams_and_stops_at_the_response_body_limit():
    class OversizedResponse:
        headers = {}

        def __init__(self):
            self.chunks_yielded = 0

        async def aiter_bytes(self, chunk_size):
            assert chunk_size == 4096
            self.chunks_yielded += 1
            yield b"a" * 16_000
            self.chunks_yielded += 1
            yield b"b" * 17_000
            self.chunks_yielded += 1
            yield b"c"

    response = OversizedResponse()
    with pytest.raises(SiteGatewayError, match="size limit"):
        await SiteGatewayClient._read_response_body(response)

    assert response.chunks_yielded == 2


@pytest.mark.asyncio
async def test_public_gateway_rejects_declared_oversized_response_before_reading_body():
    class UnreadResponse:
        headers = {"content-length": str(SiteGatewayClient.MAX_RESPONSE_BODY_BYTES + 1)}

        def __init__(self):
            self.read_started = False

        async def aiter_bytes(self, chunk_size):
            self.read_started = True
            yield b'{"answer":"ok"}'

    response = UnreadResponse()
    with pytest.raises(SiteGatewayError, match="size limit"):
        await SiteGatewayClient._read_response_body(response)

    assert response.read_started is False
