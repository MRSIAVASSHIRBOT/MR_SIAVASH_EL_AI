from datetime import UTC, datetime, timedelta

import httpx
import pytest

from el_ai.integrations.hub import HubClient, HubNotPairedError


class MemoryStore:
    def __init__(self):
        self.values = {}

    def save(self, client_id, credentials):
        self.values[client_id] = dict(credentials)

    def load(self, client_id):
        value = self.values.get(client_id)
        return dict(value) if value is not None else None

    def delete(self, client_id):
        self.values.pop(client_id, None)


@pytest.mark.asyncio
async def test_pairing_stores_credentials_and_status_uses_bound_identity():
    store = MemoryStore()
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/exchange"):
            return httpx.Response(200, json={
                "access_token": "access-secret",
                "refresh_token": "refresh-secret",
                "client_id": "client-1",
                "expires_in": 900,
                "refresh_expires_in": 86400,
                "scopes": ["status:read"],
            })
        return httpx.Response(200, json={
            "status": "authenticated",
            "client_id": "client-1",
            "scopes": ["status:read"],
        })

    client = HubClient(
        "http://127.0.0.1:8000",
        "client-1",
        store,
        transport=httpx.MockTransport(handler),
    )
    paired = await client.pair("one-time-pairing-code", 123456)
    status = await client.status(123456)

    assert paired["client_id"] == "client-1"
    assert status["status"] == "authenticated"
    assert requests[1].headers["Authorization"] == "Bearer access-secret"
    assert requests[1].headers["X-Telegram-User-Id"] == "123456"
    assert store.load("client-1")["refresh_token"] == "refresh-secret"
    await client.close()


@pytest.mark.asyncio
async def test_access_token_is_refreshed_before_expiry():
    store = MemoryStore()
    store.save("client-2", {
        "access_token": "expired-access",
        "access_expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
        "refresh_token": "refresh-secret",
        "refresh_expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        "scopes": ["status:read"],
    })
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.endswith("/refresh"):
            return httpx.Response(200, json={"access_token": "new-access", "expires_in": 900})
        return httpx.Response(200, json={"status": "authenticated"})

    client = HubClient(
        "http://localhost:8000",
        "client-2",
        store,
        transport=httpx.MockTransport(handler),
    )
    await client.status(123456)
    assert calls[0].url.path.endswith("/refresh")
    assert calls[1].headers["Authorization"] == "Bearer new-access"
    assert store.load("client-2")["access_token"] == "new-access"
    await client.close()


@pytest.mark.asyncio
async def test_unpaired_client_fails_closed():
    client = HubClient("http://127.0.0.1:8000", "client-3", MemoryStore())
    with pytest.raises(HubNotPairedError):
        await client.status(123456)
    await client.close()


@pytest.mark.asyncio
async def test_refresh_expiry_deletes_credentials():
    store = MemoryStore()
    store.save("client-4", {
        "access_token": "expired-access",
        "access_expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
        "refresh_token": "expired-refresh",
        "refresh_expires_at": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
        "scopes": [],
    })
    client = HubClient("http://127.0.0.1:8000", "client-4", store)
    with pytest.raises(HubNotPairedError):
        await client.status(123456)
    assert store.load("client-4") is None
    await client.close()


def test_remote_hub_url_requires_explicit_https_opt_in():
    with pytest.raises(ValueError, match="HTTPS"):
        HubClient("http://example.com:8000", "client-5", MemoryStore())
    with pytest.raises(ValueError, match="explicit opt-in"):
        HubClient("https://hub.example.com", "client-5", MemoryStore())
    remote = HubClient(
        "https://hub.example.com",
        "client-5",
        MemoryStore(),
        allow_remote=True,
    )
    assert remote is not None
    import asyncio
    asyncio.run(remote.close())


def test_secret_store_serializes_credentials_without_exposing_them_in_errors():
    from el_ai.integrations.secrets import KeyringCredentialStore

    class Backend:
        def __init__(self):
            self.items = {}

        def set_password(self, service, username, password):
            self.items[(service, username)] = password

        def get_password(self, service, username):
            return self.items.get((service, username))

        def delete_password(self, service, username):
            self.items.pop((service, username), None)

    backend = Backend()
    store = KeyringCredentialStore(backend)
    store.save("client-6", {"access_token": "secret-value"})
    assert store.load("client-6") == {"access_token": "secret-value"}
    assert "secret-value" not in repr(store)
    store.delete("client-6")
    assert store.load("client-6") is None


@pytest.mark.asyncio
async def test_unauthorized_access_token_is_refreshed_once_and_retried():
    store = MemoryStore()
    store.save("client-7", {
        "access_token": "server-expired-access",
        "access_expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        "refresh_token": "refresh-secret",
        "refresh_expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        "scopes": ["status:read"],
    })
    status_attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal status_attempts
        if request.url.path.endswith("/refresh"):
            return httpx.Response(200, json={"access_token": "refreshed-access", "expires_in": 900})
        status_attempts += 1
        if status_attempts == 1:
            return httpx.Response(401, json={"detail": "expired"})
        return httpx.Response(200, json={"status": "authenticated"})

    client = HubClient(
        "http://127.0.0.1:8000",
        "client-7",
        store,
        transport=httpx.MockTransport(handler),
    )
    result = await client.status(123456)
    assert result["status"] == "authenticated"
    assert status_attempts == 2
    assert store.load("client-7")["access_token"] == "refreshed-access"
    await client.close()
