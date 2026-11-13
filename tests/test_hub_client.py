import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from el_ai.integrations.hub import HubClient, HubClientError, HubNotPairedError


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



@pytest.mark.asyncio
async def test_refresh_rotation_persists_and_uses_the_replacement_refresh_token():
    store = MemoryStore()
    store.save("client-8", {
        "access_token": "expired-access",
        "access_expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
        "refresh_token": "refresh-one",
        "refresh_expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        "scopes": ["status:read"],
    })
    refresh_tokens = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/refresh"):
            import json
            payload = json.loads(request.content)
            refresh_tokens.append(payload["refresh_token"])
            generation = len(refresh_tokens)
            return httpx.Response(200, json={
                "access_token": f"access-{generation}",
                "expires_in": 900,
                "refresh_token": f"refresh-{generation + 1}",
                "refresh_expires_in": 86400,
            })
        return httpx.Response(200, json={"status": "authenticated"})

    client = HubClient(
        "http://127.0.0.1:8000",
        "client-8",
        store,
        transport=httpx.MockTransport(handler),
    )
    await client.status(123456)
    first = store.load("client-8")
    assert first["refresh_token"] == "refresh-2"

    first["access_expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    store.save("client-8", first)
    await client.status(123456)

    assert refresh_tokens == ["refresh-one", "refresh-2"]
    assert store.load("client-8")["refresh_token"] == "refresh-3"
    await client.close()


@pytest.mark.asyncio
async def test_concurrent_expired_access_requests_rotate_refresh_token_once():
    store = MemoryStore()
    store.save("client-9", {
        "access_token": "expired-access",
        "access_expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
        "refresh_token": "refresh-one",
        "refresh_expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        "scopes": ["status:read"],
    })
    refresh_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal refresh_calls
        if request.url.path.endswith("/refresh"):
            refresh_calls += 1
            return httpx.Response(200, json={
                "access_token": "access-two",
                "expires_in": 900,
                "refresh_token": "refresh-two",
                "refresh_expires_in": 86400,
            })
        return httpx.Response(200, json={"status": "authenticated"})

    client = HubClient(
        "http://127.0.0.1:8000",
        "client-9",
        store,
        transport=httpx.MockTransport(handler),
    )
    first, second = await asyncio.gather(
        client.status(123456),
        client.status(123456),
    )

    assert first["status"] == second["status"] == "authenticated"
    assert refresh_calls == 1
    assert store.load("client-9")["refresh_token"] == "refresh-two"
    await client.close()


@pytest.mark.asyncio
async def test_pairing_http_error_does_not_retain_pairing_code_context():
    pairing_code = "one-time-pairing-code-secret"

    def handler(request: httpx.Request) -> httpx.Response:
        assert pairing_code.encode() in request.content
        return httpx.Response(401, json={"detail": "rejected"})

    client = HubClient(
        "http://127.0.0.1:8000",
        "client-10",
        MemoryStore(),
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(HubClientError) as captured:
        await client.pair(pairing_code, 123456)

    assert captured.value.status_code == 401
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert pairing_code not in str(captured.value)
    await client.close()


@pytest.mark.asyncio
async def test_authorized_http_error_does_not_retain_bearer_context():
    store = MemoryStore()
    access_token = "access-token-secret"
    store.save("client-11", {
        "access_token": access_token,
        "access_expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        "refresh_token": "refresh-token-secret",
        "refresh_expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        "scopes": ["status:read"],
    })

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == f"Bearer {access_token}"
        return httpx.Response(403, json={"detail": "rejected"})

    client = HubClient(
        "http://127.0.0.1:8000",
        "client-11",
        store,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(HubClientError) as captured:
        await client.status(123456)

    assert captured.value.status_code == 403
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert access_token not in str(captured.value)
    await client.close()

@pytest.mark.asyncio
async def test_pairing_transport_error_does_not_retain_pairing_code_context():
    pairing_code = "one-time-transport-pairing-secret"

    def handler(request: httpx.Request) -> httpx.Response:
        assert pairing_code.encode() in request.content
        raise httpx.ConnectError("private transport details", request=request)

    client = HubClient(
        "http://127.0.0.1:8000",
        "client-12",
        MemoryStore(),
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(HubClientError) as captured:
        await client.pair(pairing_code, 123456)

    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert pairing_code not in str(captured.value)
    assert "private transport details" not in str(captured.value)
    await client.close()


@pytest.mark.asyncio
async def test_authorized_transport_error_does_not_retain_bearer_context():
    store = MemoryStore()
    access_token = "transport-access-token-secret"
    store.save("client-13", {
        "access_token": access_token,
        "access_expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        "refresh_token": "transport-refresh-token-secret",
        "refresh_expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        "scopes": ["status:read"],
    })

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == f"Bearer {access_token}"
        raise httpx.ConnectError("private transport details", request=request)

    client = HubClient(
        "http://127.0.0.1:8000",
        "client-13",
        store,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(HubClientError) as captured:
        await client.status(123456)

    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert access_token not in str(captured.value)
    assert "private transport details" not in str(captured.value)
    await client.close()


@pytest.mark.parametrize("operation", ["save", "load", "delete"])
def test_secret_store_errors_do_not_retain_backend_exception_context(operation):
    from el_ai.integrations.secrets import KeyringCredentialStore, SecretStoreError

    leaked_value = "credential-backend-secret-value"

    class FailingBackend:
        def set_password(self, service, username, password):
            raise RuntimeError(f"backend rejected {leaked_value}")

        def get_password(self, service, username):
            if operation == "load":
                raise RuntimeError(f"backend exposed {leaked_value}")
            return "stored-payload"

        def delete_password(self, service, username):
            raise RuntimeError(f"backend rejected {leaked_value}")

    store = KeyringCredentialStore(FailingBackend())
    with pytest.raises(SecretStoreError) as captured:
        if operation == "save":
            store.save("client-secret-test", {"access_token": leaked_value})
        elif operation == "load":
            store.load("client-secret-test")
        else:
            store.delete("client-secret-test")

    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert leaked_value not in str(captured.value)
