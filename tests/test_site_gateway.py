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
