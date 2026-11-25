import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import pytest

from el_ai.integrations.hub import HubClient, HubClientError, HubNotPairedError
from el_ai.integrations.request_context import request_correlation_scope


@pytest.mark.parametrize(
    "lifetime",
    [True, False, 1.5, "900", 0, -1, 10**1000],
)
def test_credential_lifetime_rejects_non_integer_and_unrepresentable_values(lifetime):
    with pytest.raises(HubClientError, match="invalid credential lifetime"):
        HubClient._expiry(lifetime)


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
    assert str(UUID(requests[0].headers["X-Request-ID"])) == requests[0].headers["X-Request-ID"]
    assert str(UUID(requests[1].headers["X-Request-ID"])) == requests[1].headers["X-Request-ID"]
    assert requests[0].headers["X-Request-ID"] != requests[1].headers["X-Request-ID"]
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
    refresh_request_id = calls[0].headers["X-Request-ID"]
    authorized_request_id = calls[1].headers["X-Request-ID"]
    assert str(UUID(refresh_request_id)) == refresh_request_id
    assert str(UUID(authorized_request_id)) == authorized_request_id
    assert refresh_request_id != authorized_request_id
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
    request_ids = []
    refresh_request_ids = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal status_attempts
        if request.url.path.endswith("/refresh"):
            refresh_request_ids.append(request.headers["X-Request-ID"])
            return httpx.Response(200, json={"access_token": "refreshed-access", "expires_in": 900})
        request_ids.append(request.headers["X-Request-ID"])
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
    assert len(request_ids) == 2
    assert request_ids[0] == request_ids[1]
    assert str(UUID(request_ids[0])) == request_ids[0]
    assert len(refresh_request_ids) == 1
    assert str(UUID(refresh_request_ids[0])) == refresh_request_ids[0]
    assert refresh_request_ids[0] != request_ids[0]
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
@pytest.mark.parametrize("operation", ["pair", "status"])
async def test_hub_http_error_exposes_only_validated_code_and_correlation_id(operation):
    from uuid import uuid4

    request_id = str(uuid4())
    private_detail = "api_key=private-upstream-secret"
    store = MemoryStore()
    if operation == "status":
        store.save("client-error-metadata", {
            "access_token": "access-for-test",
            "access_expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
            "refresh_token": "refresh-for-test",
            "refresh_expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            "scopes": ["status:read"],
        })

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503,
            json={
                "detail": private_detail,
                "error": {
                    "code": "upstream_failure",
                    "message": private_detail,
                    "request_id": request_id,
                },
            },
        )

    client = HubClient(
        "http://127.0.0.1:8000",
        "client-error-metadata",
        store,
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(HubClientError) as captured:
            if operation == "pair":
                await client.pair("one-time-pairing-code", 123456)
            else:
                await client.status(123456)

        assert captured.value.status_code == 503
        assert captured.value.error_code == "upstream_failure"
        assert captured.value.request_id == request_id
        assert private_detail not in str(captured.value)
        assert private_detail not in repr(captured.value)
        assert captured.value.__cause__ is None
        assert captured.value.__context__ is None
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_hub_http_error_ignores_untrusted_error_metadata():
    store = MemoryStore()
    store.save("client-invalid-error-metadata", {
        "access_token": "access-for-test",
        "access_expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        "refresh_token": "refresh-for-test",
        "refresh_expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        "scopes": ["status:read"],
    })

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503,
            json={
                "error": {
                    "code": "api_key=do-not-trust",
                    "request_id": "not-a-uuid",
                },
            },
        )

    client = HubClient(
        "http://127.0.0.1:8000",
        "client-invalid-error-metadata",
        store,
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(HubClientError) as captured:
            await client.status(123456)
        assert captured.value.error_code is None
        assert captured.value.request_id is not None
        assert str(UUID(captured.value.request_id)) == captured.value.request_id
        assert "api_key" not in str(captured.value)
    finally:
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


@pytest.mark.asyncio
async def test_task_status_uses_scoped_task_endpoint_and_identity_headers():
    store = MemoryStore()
    store.save("client-task-status", {
        "access_token": "task-status-access",
        "access_expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        "refresh_token": "task-status-refresh",
        "refresh_expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        "scopes": ["tasks:read"],
    })
    task_id = "12345678-1234-4234-8234-123456789abc"
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"task_id": task_id, "status": "succeeded"})

    client = HubClient(
        "http://127.0.0.1:8000",
        "client-task-status",
        store,
        transport=httpx.MockTransport(handler),
    )
    result = await client.get_task(123456, task_id)

    assert result == {"task_id": task_id, "status": "succeeded"}
    assert requests[0].method == "GET"
    assert requests[0].url.path == f"/integrations/telegram/tasks/{task_id}"
    assert requests[0].headers["Authorization"] == "Bearer task-status-access"
    assert requests[0].headers["X-Telegram-User-Id"] == "123456"
    await client.close()


@pytest.mark.asyncio
async def test_task_status_rejects_non_uuid_before_network_request():
    client = HubClient("http://127.0.0.1:8000", "client-task-invalid", MemoryStore())
    with pytest.raises(ValueError):
        await client.get_task(123456, "../private")
    await client.close()


@pytest.mark.asyncio
async def test_task_list_requests_the_requested_bounded_page_and_preserves_cursor():
    store = MemoryStore()
    store.save("client-task-pages", {
        "access_token": "task-pages-access",
        "access_expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        "refresh_token": "task-pages-refresh",
        "refresh_expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        "scopes": ["tasks:read"],
    })
    cursor = "MjAyNi0xMS0xNFQwMDowMDowMCswMDowMAoxMjM0NTY3OC0xMjM0LTQyMzQtODIzNC0xMjM0NTY3ODlhYmM"
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"tasks": [], "count": 0, "has_more": False, "next_cursor": None},
        )

    client = HubClient(
        "http://127.0.0.1:8000",
        "client-task-pages",
        store,
        transport=httpx.MockTransport(handler),
    )
    result = await client.list_tasks(123456, cursor=cursor, limit=17, status="queued")

    assert result["has_more"] is False
    assert requests[0].method == "GET"
    assert requests[0].url.path == "/integrations/telegram/tasks"
    assert requests[0].url.params["limit"] == "17"
    assert requests[0].url.params["cursor"] == cursor
    assert requests[0].url.params["status"] == "queued"
    assert requests[0].headers["Authorization"] == "Bearer task-pages-access"
    assert requests[0].headers["X-Telegram-User-Id"] == "123456"
    await client.close()


@pytest.mark.asyncio
async def test_task_list_rejects_invalid_cursor_and_limit_before_network_request():
    client = HubClient("http://127.0.0.1:8000", "client-task-pages-invalid", MemoryStore())
    with pytest.raises(ValueError):
        await client.list_tasks(123456, cursor="../private")
    with pytest.raises(ValueError):
        await client.list_tasks(123456, limit=51)
    with pytest.raises(ValueError):
        await client.list_tasks(123456, status="unknown")
    await client.close()



@pytest.mark.asyncio
async def test_task_result_uses_client_scoped_result_endpoint():
    store = MemoryStore()
    store.save("client-task-result", {
        "access_token": "task-result-access",
        "access_expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        "refresh_token": "task-result-refresh",
        "refresh_expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        "scopes": ["tasks:result"],
    })
    task_id = "12345678-1234-4234-8234-123456789abc"
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"result": {"message": "done"}})

    client = HubClient(
        "http://127.0.0.1:8000",
        "client-task-result",
        store,
        transport=httpx.MockTransport(handler),
    )
    result = await client.get_task_result(123456, task_id)

    assert result == {"result": {"message": "done"}}
    assert requests[0].method == "GET"
    assert requests[0].url.path == f"/integrations/telegram/tasks/{task_id}/result"
    assert requests[0].headers["X-Telegram-User-Id"] == "123456"
    await client.close()


@pytest.mark.asyncio
async def test_task_cancel_posts_to_client_scoped_endpoint():
    store = MemoryStore()
    store.save("client-task-cancel", {
        "access_token": "task-cancel-access",
        "access_expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        "refresh_token": "task-cancel-refresh",
        "refresh_expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        "scopes": ["tasks:cancel"],
    })
    task_id = "12345678-1234-4234-8234-123456789abc"
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"task_id": task_id, "status": "cancelled"})

    client = HubClient(
        "http://127.0.0.1:8000",
        "client-task-cancel",
        store,
        transport=httpx.MockTransport(handler),
    )
    result = await client.cancel_task(123456, task_id)

    assert result == {"task_id": task_id, "status": "cancelled"}
    assert requests[0].method == "POST"
    assert requests[0].url.path == f"/integrations/telegram/tasks/{task_id}/cancel"
    assert requests[0].headers["X-Telegram-User-Id"] == "123456"
    assert requests[0].content == b"{}"
    await client.close()


@pytest.mark.asyncio
async def test_task_result_and_cancel_reject_invalid_ids_before_network_request():
    client = HubClient("http://127.0.0.1:8000", "client-task-control-invalid", MemoryStore())
    with pytest.raises(ValueError):
        await client.get_task_result(123456, "../private")
    with pytest.raises(ValueError):
        await client.cancel_task(123456, "../private")
    await client.close()


@pytest.mark.asyncio
async def test_task_history_uses_the_pair_scoped_audit_endpoint():
    store = MemoryStore()
    store.save("client-task-history", {
        "access_token": "task-history-access",
        "access_expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        "refresh_token": "task-history-refresh",
        "refresh_expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        "scopes": ["tasks:read"],
    })
    task_id = "12345678-1234-4234-8234-123456789abc"
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={
            "task_id": task_id,
            "events": [{"event_type": "task.submitted", "outcome": "queued"}],
            "count": 1,
        })

    client = HubClient(
        "http://127.0.0.1:8000",
        "client-task-history",
        store,
        transport=httpx.MockTransport(handler),
    )
    result = await client.get_task_history(123456, task_id, limit=12, cursor="cursor_2-next")

    assert result["count"] == 1
    assert requests[0].method == "GET"
    assert requests[0].url.path == f"/integrations/telegram/tasks/{task_id}/events"
    assert requests[0].url.params["limit"] == "12"
    assert requests[0].url.params["cursor"] == "cursor_2-next"
    assert requests[0].headers["X-Telegram-User-Id"] == "123456"
    await client.close()


@pytest.mark.asyncio
async def test_task_history_rejects_invalid_id_and_limit_before_network_request():
    client = HubClient("http://127.0.0.1:8000", "client-task-history-invalid", MemoryStore())
    with pytest.raises(ValueError):
        await client.get_task_history(123456, "../private")
    with pytest.raises(ValueError):
        await client.get_task_history(123456, "12345678-1234-4234-8234-123456789abc", limit=51)
    with pytest.raises(ValueError):
        await client.get_task_history(
            123456, "12345678-1234-4234-8234-123456789abc", cursor="bad cursor"
        )
    await client.close()



@pytest.mark.asyncio
async def test_hub_request_logs_are_correlated_and_privacy_safe(caplog):
    store = MemoryStore()
    store.save("client-log-correlation", {
        "access_token": "log-access-secret",
        "access_expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        "refresh_token": "log-refresh-secret",
        "refresh_expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        "scopes": ["tasks:read"],
    })
    task_id = "12345678-1234-4234-8234-123456789abc"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"task_id": task_id, "events": [], "count": 0})

    client = HubClient(
        "http://127.0.0.1:8000",
        "client-log-correlation",
        store,
        transport=httpx.MockTransport(handler),
    )
    with caplog.at_level("INFO", logger="el_ai.hub"):
        await client.get_task_history(123456, task_id, limit=7)

    record = next(record for record in caplog.records if record.name == "el_ai.hub")
    assert str(UUID(record.request_id)) == record.request_id
    assert record.method == "GET"
    assert record.route_template == "/integrations/telegram/tasks/{task_id}/events"
    assert record.status_code == 200
    assert record.outcome == "success"
    assert isinstance(record.duration_ms, (int, float))
    assert record.duration_ms >= 0
    assert task_id not in caplog.text
    assert "limit=7" not in caplog.text
    assert "123456" not in caplog.text
    assert "log-access-secret" not in caplog.text
    assert "log-refresh-secret" not in caplog.text
    await client.close()


@pytest.mark.asyncio
async def test_video_generation_client_uses_scoped_routes_and_bounded_pagination():
    store = MemoryStore()
    store.save("video-client", {
        "access_token": "video-access",
        "access_expires_at": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
        "refresh_token": "video-refresh",
        "refresh_expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        "scopes": ["video:read", "video:cancel"],
    })
    generation_id = "12345678-1234-4234-8234-123456789abc"
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.endswith("/video/providers"):
            return httpx.Response(200, json={
                "providers": [], "count": 0, "generation_available": False,
                "reason_code": "provider_unavailable", "execution": "not_started"
            })
        if request.method == "DELETE":
            return httpx.Response(200, json={
                "generation_id": generation_id, "status": "cancelled",
                "cancellation_requested": False
            })
        if request.url.path.endswith("/" + generation_id):
            return httpx.Response(200, json={
                "generation_id": generation_id, "status": "queued"
            })
        return httpx.Response(200, json={
            "items": [], "count": 0, "has_more": False, "next_cursor": None
        })

    client = HubClient(
        "http://127.0.0.1:8000",
        "video-client",
        store,
        transport=httpx.MockTransport(handler),
    )
    providers = await client.list_video_providers(123)
    history = await client.list_video_generations(123, cursor="cursor_123", limit=10)
    status = await client.get_video_generation(123, generation_id)
    cancelled = await client.cancel_video_generation(123, generation_id)

    assert providers["generation_available"] is False
    assert history["count"] == 0
    assert status["status"] == "queued"
    assert cancelled["status"] == "cancelled"
    assert calls[0].url.path == "/integrations/telegram/video/providers"
    assert calls[1].url.path == "/integrations/telegram/video/generations"
    assert calls[1].url.params["cursor"] == "cursor_123"
    assert calls[1].url.params["limit"] == "10"
    assert calls[2].url.path.endswith("/" + generation_id)
    assert calls[3].method == "DELETE"
    assert all(call.headers["Authorization"] == "Bearer video-access" for call in calls)
    assert HubClient._route_template(calls[2].url.path) == (
        "/integrations/telegram/video/generations/{generation_id}"
    )
    await client.close()


@pytest.mark.asyncio
async def test_video_generation_client_rejects_invalid_cursor_and_identifiers():
    client = HubClient(
        "http://127.0.0.1:8000", "video-invalid", MemoryStore()
    )
    with pytest.raises(ValueError, match="cursor"):
        await client.list_video_generations(123, cursor="invalid!")
    with pytest.raises(ValueError):
        await client.get_video_generation(123, "not-a-uuid")
    with pytest.raises(ValueError):
        await client.cancel_video_generation(123, "not-a-uuid")
    with pytest.raises(ValueError, match="limit"):
        await client.list_video_generations(123, limit=21)
    await client.close()


@pytest.mark.asyncio
async def test_video_route_preview_client_uses_video_read_scope_and_bounded_payload():
    store = MemoryStore()
    store.save("route-client", {
        "access_token": "route-access",
        "access_expires_at": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
        "refresh_token": "route-refresh",
        "refresh_expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        "scopes": ["video:read"],
    })
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={
            "decision": {"selected": False, "reason": "no_compatible_provider"},
            "execution": "not_started",
            "provider_submission_started": False,
        })

    client = HubClient(
        "http://127.0.0.1:8000",
        "route-client",
        store,
        transport=httpx.MockTransport(handler),
    )
    result = await client.preview_video_route(
        123,
        duration_seconds=10,
        aspect_ratio="9:16",
        remote_processing_approved=True,
        remote_spend_approved=True,
        max_remote_cost_usd=0.5,
    )

    assert result["execution"] == "not_started"
    assert calls[0].method == "POST"
    assert calls[0].url.path == "/integrations/telegram/video/route-preview"
    assert json.loads(calls[0].content) == {
        "duration_seconds": 10,
        "aspect_ratio": "9:16",
        "resolution": "720p",
        "remote_processing_approved": True,
        "remote_spend_approved": True,
        "max_remote_cost_usd": 0.5,
    }
    assert calls[0].headers["Authorization"] == "Bearer route-access"
    assert HubClient._route_template(calls[0].url.path) == (
        "/integrations/telegram/video/route-preview"
    )
    await client.close()


@pytest.mark.asyncio
async def test_video_route_preview_client_rejects_invalid_duration_cost_and_consent():
    client = HubClient(
        "http://127.0.0.1:8000", "route-invalid", MemoryStore()
    )
    with pytest.raises(ValueError, match="duration"):
        await client.preview_video_route(123, duration_seconds=True, aspect_ratio="9:16")
    with pytest.raises(ValueError, match="aspect"):
        await client.preview_video_route(123, duration_seconds=10, aspect_ratio="2:1")
    with pytest.raises(ValueError, match="consent"):
        await client.preview_video_route(
            123, duration_seconds=10, aspect_ratio="9:16",
            remote_processing_approved="yes"
        )
    with pytest.raises(ValueError, match="cost"):
        await client.preview_video_route(
            123, duration_seconds=10, aspect_ratio="9:16",
            max_remote_cost_usd=float("inf")
        )
    await client.close()



@pytest.mark.asyncio
async def test_memory_dream_preview_uses_paired_private_endpoint_and_bounds():
    store = MemoryStore()
    store.save("client-memory-dream", {
        "access_token": "memory-dream-access",
        "access_expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        "refresh_token": "memory-dream-refresh",
        "refresh_expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        "scopes": ["memory:search"],
    })
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={
            "engine": "deterministic_memory_review_v1",
            "proposal_count": 0,
            "state_changed": False,
            "proposals": [],
        })

    client = HubClient(
        "http://127.0.0.1:8000",
        "client-memory-dream",
        store,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ValueError):
        await client.preview_memory_dream(123456, limit=101)
    with pytest.raises(ValueError):
        await client.preview_memory_dream(123456, similarity_threshold=0.5)

    result = await client.preview_memory_dream(123456)
    assert result["engine"] == "deterministic_memory_review_v1"
    assert requests[0].method == "POST"
    assert requests[0].url.path == "/integrations/telegram/memory/dream/preview"
    assert requests[0].headers["X-Telegram-User-Id"] == "123456"
    assert requests[0].read().decode() == (
        '{"limit":100,"similarity_threshold":0.82}'
    )
    await client.close()



@pytest.mark.asyncio
async def test_memory_validation_preview_uses_paired_private_endpoint_and_bounds():
    store = MemoryStore()
    store.save("client-memory-validation", {
        "access_token": "memory-validation-access",
        "access_expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        "refresh_token": "memory-validation-refresh",
        "refresh_expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        "scopes": ["memory:search"],
    })
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={
            "engine": "deterministic_memory_validation_v1",
            "contradiction_count": 0,
            "unverified_count": 0,
            "state_changed": False,
        })

    client = HubClient(
        "http://127.0.0.1:8000",
        "client-memory-validation",
        store,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ValueError):
        await client.preview_memory_validation(123456, limit=101)
    with pytest.raises(ValueError):
        await client.preview_memory_validation(123456, similarity_threshold=0.5)

    result = await client.preview_memory_validation(123456)
    assert result["engine"] == "deterministic_memory_validation_v1"
    assert requests[0].method == "POST"
    assert requests[0].url.path == "/integrations/telegram/memory/validate/preview"
    assert requests[0].headers["X-Telegram-User-Id"] == "123456"
    assert requests[0].read().decode() == (
        '{"limit":100,"similarity_threshold":0.72}'
    )
    await client.close()


@pytest.mark.asyncio
async def test_memory_entry_read_and_explicit_approval_use_distinct_scoped_routes():
    memory_id = "12345678-1234-4234-8234-123456789abc"
    read_store = MemoryStore()
    read_store.save("client-memory-entry", {
        "access_token": "memory-entry-access",
        "access_expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        "refresh_token": "memory-entry-refresh",
        "refresh_expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        "scopes": ["memory:search"],
    })
    read_requests = []

    def read_handler(request: httpx.Request) -> httpx.Response:
        read_requests.append(request)
        return httpx.Response(200, json={
            "memory_id": memory_id,
            "category": "preferences",
            "content": "Concise summaries are preferred",
            "verification_status": "user_authored",
        })

    read_client = HubClient(
        "http://127.0.0.1:8000",
        "client-memory-entry",
        read_store,
        transport=httpx.MockTransport(read_handler),
    )
    entry = await read_client.get_memory_entry(123456, memory_id)
    assert entry["verification_status"] == "user_authored"
    assert read_requests[0].method == "GET"
    assert read_requests[0].url.path == f"/integrations/telegram/memory/{memory_id}"
    assert HubClient._route_template(read_requests[0].url.path) == (
        "/integrations/telegram/memory/{memory_id}"
    )
    await read_client.close()

    write_store = MemoryStore()
    write_store.save("client-memory-approve", {
        "access_token": "memory-approve-access",
        "access_expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        "refresh_token": "memory-approve-refresh",
        "refresh_expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        "scopes": ["memory:write"],
    })
    write_requests = []

    def write_handler(request: httpx.Request) -> httpx.Response:
        write_requests.append(request)
        return httpx.Response(200, json={
            "memory_id": memory_id,
            "verification_status": "user_approved",
            "changed": True,
        })

    write_client = HubClient(
        "http://127.0.0.1:8000",
        "client-memory-approve",
        write_store,
        transport=httpx.MockTransport(write_handler),
    )
    approved = await write_client.approve_memory_entry(123456, memory_id)
    assert approved["changed"] is True
    assert write_requests[0].method == "POST"
    assert write_requests[0].url.path == (
        f"/integrations/telegram/memory/{memory_id}/approve"
    )
    assert HubClient._route_template(write_requests[0].url.path) == (
        "/integrations/telegram/memory/{memory_id}/approve"
    )
    await write_client.close()


@pytest.mark.asyncio
async def test_video_render_preflight_uses_owner_scoped_read_route():
    store = MemoryStore()
    store.save("client-preflight", {
        "access_token": "access-token",
        "access_expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        "refresh_token": "refresh-token",
        "refresh_expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        "scopes": ["video:read"],
    })
    draft_id = "12345678-1234-4234-8234-123456789abc"
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={
            "draft_id": draft_id,
            "ready": True,
            "execution": "not_started",
            "state_changed": False,
            "checks": [],
        })

    client = HubClient(
        "http://127.0.0.1:8000",
        "client-preflight",
        store,
        transport=httpx.MockTransport(handler),
    )
    result = await client.preflight_video_render(123456, draft_id)
    assert result["ready"] is True
    assert requests[0].method == "GET"
    assert requests[0].url.path == "/integrations/telegram/video/render-preflight"
    assert requests[0].url.params["draft_id"] == draft_id
    assert requests[0].headers["X-Telegram-User-Id"] == "123456"
    assert HubClient._route_template(requests[0].url.path + "?" + requests[0].url.query.decode()) == (
        "/integrations/telegram/video/render-preflight"
    )
    await client.close()


@pytest.mark.asyncio
async def test_video_render_preflight_rejects_invalid_draft_id_before_request():
    client = HubClient("http://127.0.0.1:8000", "client-invalid", MemoryStore())
    with pytest.raises(ValueError):
        await client.preflight_video_render(123456, "not-a-uuid")
    await client.close()


@pytest.mark.asyncio
async def test_video_render_submit_preflight_uses_video_render_scope_route():
    store = MemoryStore()
    store.save("client-render-preflight", {
        "access_token": "access-token",
        "access_expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        "refresh_token": "refresh-token",
        "refresh_expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        "scopes": ["video:render"],
    })
    draft_id = "12345678-1234-4234-8234-123456789abc"
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"draft_id": draft_id, "ready": True, "checks": []})

    client = HubClient(
        "http://127.0.0.1:8000",
        "client-render-preflight",
        store,
        transport=httpx.MockTransport(handler),
    )
    result = await client.preflight_video_render_for_submit(123456, draft_id)
    assert result["ready"] is True
    assert requests[0].method == "GET"
    assert requests[0].url.path == "/integrations/telegram/video/render-preflight-submit"
    assert requests[0].url.params["draft_id"] == draft_id
    await client.close()


@pytest.mark.asyncio
async def test_bridge_correlation_id_is_propagated_without_reusing_request_id():
    store = MemoryStore()
    store.save("client-x7-correlation", {
        "access_token": "correlation-access",
        "access_expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        "refresh_token": "correlation-refresh",
        "refresh_expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        "scopes": ["status:read"],
    })
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"status": "authenticated"})

    client = HubClient(
        "http://127.0.0.1:8000",
        "client-x7-correlation",
        store,
        transport=httpx.MockTransport(handler),
    )
    from uuid import UUID, uuid4

    correlation_id = str(uuid4())
    with request_correlation_scope(correlation_id):
        await client.status(123456)

    assert len(requests) == 1
    assert requests[0].headers["X-Correlation-ID"] == correlation_id
    request_id = requests[0].headers["X-Request-ID"]
    assert str(UUID(request_id)) == request_id
    assert request_id != correlation_id
    await client.close()


@pytest.mark.asyncio
async def test_pairing_request_carries_a_canonical_request_id():
    from uuid import UUID

    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={
            "access_token": "access-secret",
            "refresh_token": "refresh-secret",
            "client_id": "client-correlation",
            "expires_in": 900,
            "refresh_expires_in": 86400,
            "scopes": ["status:read"],
        })

    client = HubClient(
        "http://127.0.0.1:8000",
        "client-correlation",
        MemoryStore(),
        transport=httpx.MockTransport(handler),
    )
    await client.pair("one-time-code", 123456)

    request_id = requests[0].headers["X-Request-ID"]
    assert str(UUID(request_id)) == request_id
    await client.close()


@pytest.mark.asyncio
async def test_authorized_request_reuses_request_id_for_401_retry_but_refresh_gets_its_own():
    from uuid import UUID

    store = MemoryStore()
    store.save("client-correlation-retry", {
        "access_token": "initial-access",
        "access_expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        "refresh_token": "refresh-secret",
        "refresh_expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        "scopes": ["status:read"],
    })
    requests = []
    status_attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal status_attempts
        requests.append(request)
        if request.url.path.endswith("/refresh"):
            return httpx.Response(200, json={"access_token": "refreshed-access", "expires_in": 900})
        status_attempts += 1
        if status_attempts == 1:
            return httpx.Response(401, json={"detail": "expired"})
        return httpx.Response(200, json={"status": "authenticated"})

    client = HubClient(
        "http://127.0.0.1:8000",
        "client-correlation-retry",
        store,
        transport=httpx.MockTransport(handler),
    )
    await client.status(123456)

    status_ids = [
        request.headers["X-Request-ID"]
        for request in requests
        if request.url.path.endswith("/status")
    ]
    refresh_id = next(
        request.headers["X-Request-ID"]
        for request in requests
        if request.url.path.endswith("/refresh")
    )
    assert len(status_ids) == 2
    assert status_ids[0] == status_ids[1]
    assert status_ids[0] != refresh_id
    assert str(UUID(status_ids[0])) == status_ids[0]
    assert str(UUID(refresh_id)) == refresh_id
    await client.close()


@pytest.mark.asyncio
async def test_agent_plan_preview_uses_scoped_route_and_explicit_provider_mode():
    store = MemoryStore()
    store.save("client-plan-preview", {
        "access_token": "plan-access",
        "access_expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        "refresh_token": "plan-refresh",
        "refresh_expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        "scopes": ["tasks:create"],
    })
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={
            "provider_mode": "remote",
            "provider": "kimi",
            "model": "test-model",
            "steps": [{"description": "مرحله اول"}, {"description": "مرحله دوم"}],
            "execution_started": False,
            "tasks_created": 0,
        })

    client = HubClient(
        "http://127.0.0.1:8000",
        "client-plan-preview",
        store,
        transport=httpx.MockTransport(handler),
    )
    result = await client.preview_agent_plan(
        123456,
        "ساخت یک برنامهٔ آزمایشی",
        provider_mode="remote",
    )

    assert result["tasks_created"] == 0
    assert result["execution_started"] is False
    assert requests[0].url.path == "/integrations/telegram/agent/plan-preview"
    assert json.loads(requests[0].content) == {
        "goal": "ساخت یک برنامهٔ آزمایشی",
        "provider_mode": "remote",
    }
    assert requests[0].headers["Authorization"] == "Bearer plan-access"
    assert requests[0].headers["X-Telegram-User-Id"] == "123456"
    await client.close()


@pytest.mark.asyncio
async def test_agent_plan_preview_rejects_invalid_goal_or_provider_mode():
    client = HubClient("http://127.0.0.1:8000", "client-invalid-plan", MemoryStore())
    with pytest.raises(ValueError):
        await client.preview_agent_plan(123456, " ")
    with pytest.raises(ValueError):
        await client.preview_agent_plan(123456, "valid goal", provider_mode="automatic")
    await client.close()


@pytest.mark.asyncio
async def test_authorized_hub_request_does_not_follow_redirects():
    store = MemoryStore()
    access_token = "redirect-access-token-secret"
    store.save("client-redirect", {
        "access_token": access_token,
        "access_expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        "refresh_token": "redirect-refresh-token-secret",
        "refresh_expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        "scopes": ["status:read"],
    })
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            302,
            headers={"Location": "https://redirect.example/collect"},
        )

    client = HubClient(
        "https://hub.example.com",
        "client-redirect",
        store,
        allow_remote=True,
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(HubClientError) as captured:
            await client.status(123456)

        assert captured.value.status_code == 302
        assert len(requests) == 1
        assert requests[0].url.host == "hub.example.com"
        assert requests[0].headers["Authorization"] == f"Bearer {access_token}"
    finally:
        await client.close()



def test_render_private_network_hub_url_requires_explicit_allowlist():
    import asyncio

    with pytest.raises(ValueError, match="explicitly allowed"):
        HubClient("http://mr-siavash-hub-ai:10000", "client-private", MemoryStore())

    client = HubClient(
        "http://mr-siavash-hub-ai:10000",
        "client-private",
        MemoryStore(),
        allow_render_private_network=True,
    )
    asyncio.run(client.close())

    with pytest.raises(ValueError, match="explicitly allowed"):
        HubClient(
            "http://hub.example.com:10000",
            "client-public",
            MemoryStore(),
            allow_render_private_network=True,
        )
    with pytest.raises(ValueError, match="explicitly allowed"):
        HubClient(
            "http://123:10000",
            "client-numeric-host",
            MemoryStore(),
            allow_render_private_network=True,
        )
    with pytest.raises(ValueError, match="explicitly allowed"):
        HubClient(
            "http://mr-siavash-hub-ai:0",
            "client-invalid-port",
            MemoryStore(),
            allow_render_private_network=True,
        )
