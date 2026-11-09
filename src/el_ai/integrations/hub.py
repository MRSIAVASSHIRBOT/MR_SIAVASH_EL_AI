"""Scoped client for the Hub's dedicated Telegram integration API."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import httpx

from el_ai.integrations.secrets import KeyringCredentialStore


class HubClientError(RuntimeError):
    """Sanitized Hub integration failure."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class HubNotPairedError(HubClientError):
    """Raised when this bot installation has no active paired credential."""


class HubClient:
    def __init__(
        self,
        base_url: str,
        client_id: str,
        credential_store: KeyringCredentialStore,
        *,
        timeout_seconds: float = 8.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        parsed = urlsplit(base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Hub API URL must be a clean HTTP(S) base URL")
        if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Non-loopback Hub API URLs must use HTTPS")
        self.client_id = client_id
        self._store = credential_store
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/",
            timeout=httpx.Timeout(timeout_seconds),
            transport=transport,
        )

    async def _request(self, method: str, path: str, payload: dict[str, object]) -> dict[str, object]:
        try:
            response = await self._client.request(method, path.lstrip("/"), json=payload)
            response.raise_for_status()
            result = response.json()
        except httpx.HTTPStatusError as exc:
            raise HubClientError(
                f"Hub rejected the request (HTTP {exc.response.status_code})",
                status_code=exc.response.status_code,
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise HubClientError("Hub API request failed") from exc
        if not isinstance(result, dict):
            raise HubClientError("Hub returned an invalid response")
        return result

    @staticmethod
    def _expiry(seconds: object) -> str:
        if not isinstance(seconds, int) or seconds < 1:
            raise HubClientError("Hub returned an invalid credential lifetime")
        return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()

    async def pair(self, pairing_code: str, telegram_user_id: int) -> dict[str, object]:
        result = await self._request(
            "POST",
            "/integrations/telegram/exchange",
            {
                "pairing_code": pairing_code,
                "telegram_user_id": telegram_user_id,
                "client_id": self.client_id,
            },
        )
        access_token = result.get("access_token")
        refresh_token = result.get("refresh_token")
        if not isinstance(access_token, str) or not isinstance(refresh_token, str):
            raise HubClientError("Hub did not issue the expected scoped credentials")
        credentials = {
            "access_token": access_token,
            "access_expires_at": self._expiry(result.get("expires_in")),
            "refresh_token": refresh_token,
            "refresh_expires_at": self._expiry(result.get("refresh_expires_in")),
            "scopes": result.get("scopes", []),
        }
        try:
            self._store.save(self.client_id, credentials)
        except Exception as exc:
            raise HubClientError("Could not store Hub credentials in the operating-system vault") from exc
        return {"client_id": self.client_id, "scopes": credentials["scopes"]}

    async def _access_token(self, telegram_user_id: int) -> str:
        try:
            credentials = self._store.load(self.client_id)
        except Exception as exc:
            raise HubClientError("Could not read Hub credentials from the operating-system vault") from exc
        if credentials is None:
            raise HubNotPairedError("This EL_AI installation is not paired with the Hub")
        access_token = credentials.get("access_token")
        access_expiry = credentials.get("access_expires_at")
        refresh_token = credentials.get("refresh_token")
        refresh_expiry = credentials.get("refresh_expires_at")
        if not all(isinstance(value, str) for value in (
            access_token, access_expiry, refresh_token, refresh_expiry
        )):
            raise HubClientError("Stored Hub credentials are incomplete")
        now = datetime.now(UTC)
        try:
            access_deadline = datetime.fromisoformat(access_expiry)
            refresh_deadline = datetime.fromisoformat(refresh_expiry)
        except ValueError as exc:
            raise HubClientError("Stored Hub credential expiry is invalid") from exc
        if access_deadline.tzinfo is None or refresh_deadline.tzinfo is None:
            raise HubClientError("Stored Hub credential expiry must be timezone-aware")
        if access_deadline > now + timedelta(seconds=30):
            return access_token
        if refresh_deadline <= now:
            self._store.delete(self.client_id)
            raise HubNotPairedError("Hub pairing expired; pair this installation again")
        try:
            refreshed = await self._request(
                "POST",
                "/integrations/telegram/refresh",
                {"refresh_token": refresh_token, "telegram_user_id": telegram_user_id},
            )
        except HubClientError:
            raise
        new_access = refreshed.get("access_token")
        if not isinstance(new_access, str):
            raise HubClientError("Hub did not return a refreshed access credential")
        credentials["access_token"] = new_access
        credentials["access_expires_at"] = self._expiry(refreshed.get("expires_in"))
        try:
            self._store.save(self.client_id, credentials)
        except Exception as exc:
            raise HubClientError("Could not rotate Hub credentials in the operating-system vault") from exc
        return new_access

    async def _authorized(
        self, method: str, path: str, telegram_user_id: int,
        payload: dict[str, object] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, object]:
        token = await self._access_token(telegram_user_id)
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Telegram-User-Id": str(telegram_user_id),
        }
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        try:
            request_kwargs = {"headers": headers}
            if payload is not None:
                request_kwargs["json"] = payload
            response = await self._client.request(
                method, path.lstrip("/"), **request_kwargs
            )
            response.raise_for_status()
            result = response.json()
        except httpx.HTTPStatusError as exc:
            raise HubClientError(f"Hub rejected the request (HTTP {exc.response.status_code})") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise HubClientError("Hub API request failed") from exc
        if not isinstance(result, dict):
            raise HubClientError("Hub returned an invalid response")
        return result

    async def status(self, telegram_user_id: int) -> dict[str, object]:
        return await self._authorized(
            "GET", "/integrations/telegram/status", telegram_user_id
        )

    async def list_tasks(self, telegram_user_id: int) -> dict[str, object]:
        return await self._authorized(
            "GET", "/integrations/telegram/tasks", telegram_user_id
        )

    async def search_memory(self, telegram_user_id: int, query: str) -> dict[str, object]:
        return await self._authorized(
            "POST",
            "/integrations/telegram/memory/search",
            telegram_user_id,
            {"query": query, "limit": 5},
        )

    async def create_task(
        self, telegram_user_id: int, task_type: str, description: str, idempotency_key: str
    ) -> dict[str, object]:
        return await self._authorized(
            "POST",
            "/integrations/telegram/tasks",
            telegram_user_id,
            {"task_type": task_type, "description": description},
            idempotency_key=idempotency_key,
        )

    async def close(self) -> None:
        await self._client.aclose()
