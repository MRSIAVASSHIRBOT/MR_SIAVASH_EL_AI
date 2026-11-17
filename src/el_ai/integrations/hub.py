"""Scoped client for the Hub's dedicated Telegram integration API."""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode, urlsplit
from uuid import UUID, uuid4

import httpx

from el_ai.integrations.secrets import KeyringCredentialStore


logger = logging.getLogger("el_ai.hub")
MAX_TELEGRAM_VIDEO_BYTES = 48_000_000

_STATIC_HUB_ROUTES = frozenset({
    "/integrations/telegram/exchange",
    "/integrations/telegram/refresh",
    "/integrations/telegram/status",
    "/integrations/telegram/tasks",
    "/integrations/telegram/memory/search",
    "/integrations/telegram/memory/dream/preview",
    "/integrations/telegram/video/providers",
    "/integrations/telegram/video/route-preview",
    "/integrations/telegram/video/generations",
    "/integrations/telegram/video/render-drafts",
    "/integrations/telegram/video/renders",
})


class HubClientError(RuntimeError):
    """Sanitized Hub integration failure."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class HubNotPairedError(HubClientError):
    """Raised when this bot installation has no active paired credential."""


class HubClient:
    @staticmethod
    def _route_template(path: str) -> str:
        path_only = urlsplit(path).path
        if path_only in _STATIC_HUB_ROUTES:
            return path_only
        video_prefix = "/integrations/telegram/video/generations/"
        if path_only.startswith(video_prefix):
            suffix = path_only[len(video_prefix):].split("/")
            if len(suffix) == 1 and suffix[0]:
                return f"{video_prefix}{{generation_id}}"
        render_prefix = "/integrations/telegram/video/renders/"
        if path_only.startswith(render_prefix):
            suffix = path_only[len(render_prefix):].split("/")
            if len(suffix) == 1 and suffix[0]:
                return f"{render_prefix}{{render_id}}"
            if len(suffix) == 2 and suffix[0] and suffix[1] == "file":
                return f"{render_prefix}{{render_id}}/file"
        task_prefix = "/integrations/telegram/tasks/"
        if path_only.startswith(task_prefix):
            suffix = path_only[len(task_prefix):].split("/")
            if len(suffix) == 1 and suffix[0]:
                return f"{task_prefix}{{task_id}}"
            if len(suffix) == 2 and suffix[0] and suffix[1] in {
                "cancel", "events", "result"
            }:
                return f"{task_prefix}{{task_id}}/{suffix[1]}"
        return "unknown"

    @staticmethod
    def _log_request(
        request_id: str,
        method: str,
        path: str,
        status_code: int | None,
        outcome: str,
    ) -> None:
        route_template = HubClient._route_template(path)
        logger.info(
            "hub_request_completed request_id=%s method=%s route_template=%s status_code=%s outcome=%s",
            request_id,
            method,
            route_template,
            status_code,
            outcome,
            extra={
                "request_id": request_id,
                "method": method,
                "route_template": route_template,
                "status_code": status_code,
                "outcome": outcome,
            },
        )

    def __init__(
        self,
        base_url: str,
        client_id: str,
        credential_store: KeyringCredentialStore,
        *,
        timeout_seconds: float = 8.0,
        transport: httpx.AsyncBaseTransport | None = None,
        allow_remote: bool = False,
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
        is_loopback = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        if not is_loopback and (parsed.scheme != "https" or not allow_remote):
            raise ValueError("Remote Hub URLs require HTTPS and explicit opt-in")
        self.client_id = client_id
        self._store = credential_store
        self._refresh_lock = asyncio.Lock()
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/",
            timeout=httpx.Timeout(timeout_seconds),
            transport=transport,
        )

    async def _request(self, method: str, path: str, payload: dict[str, object]) -> dict[str, object]:
        request_id = str(uuid4())
        status_code: int | None = None
        response_status_code: int | None = None
        failed = False
        result: dict[str, object] | None = None
        try:
            response = await self._client.request(
                method,
                path.lstrip("/"),
                json=payload,
                headers={"X-Request-ID": request_id},
            )
            response_status_code = response.status_code
            response.raise_for_status()
            result = response.json()
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
        except (httpx.HTTPError, ValueError):
            failed = True
        outcome = (
            "http_error"
            if status_code is not None and status_code >= 400
            else "request_failed"
            if failed
            else "invalid_response"
            if not isinstance(result, dict)
            else "success"
        )
        self._log_request(request_id, method, path, response_status_code or status_code, outcome)
        if status_code is not None:
            raise HubClientError(
                f"Hub rejected the request (HTTP {status_code})",
                status_code=status_code,
            )
        if failed:
            raise HubClientError("Hub API request failed")
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
        if result.get("client_id") != self.client_id:
            raise HubClientError("Hub pairing response is bound to a different client")
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
        # Refresh-token rotation is single-use. Serialize refreshes and reload
        # credentials after acquiring the lock so concurrent callers reuse the
        # token produced by the first successful refresh.
        async with self._refresh_lock:
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
                try:
                    self._store.delete(self.client_id)
                except Exception as exc:
                    raise HubClientError("Expired Hub credentials could not be removed from secure storage") from exc
                raise HubNotPairedError("Hub pairing expired; pair this installation again")
            try:
                refreshed = await self._request(
                    "POST",
                    "/integrations/telegram/refresh",
                    {"refresh_token": refresh_token, "telegram_user_id": telegram_user_id},
                )
            except HubClientError as exc:
                if exc.status_code in {401, 403}:
                    try:
                        self._store.delete(self.client_id)
                    except Exception as store_exc:
                        raise HubClientError("Hub credential revocation could not be synchronized") from store_exc
                    raise HubNotPairedError("Hub pairing was revoked or is no longer valid") from exc
                raise
            new_access = refreshed.get("access_token")
            if not isinstance(new_access, str) or not new_access:
                raise HubClientError("Hub did not return a refreshed access credential")
            credentials["access_token"] = new_access
            credentials["access_expires_at"] = self._expiry(refreshed.get("expires_in"))
            # Older Hub versions may omit rotation fields; when present, the
            # replacement refresh token and lifetime must be persisted together.
            new_refresh = refreshed.get("refresh_token")
            if new_refresh is not None:
                if not isinstance(new_refresh, str) or not new_refresh:
                    raise HubClientError("Hub returned an invalid rotated refresh credential")
                credentials["refresh_token"] = new_refresh
                credentials["refresh_expires_at"] = self._expiry(
                    refreshed.get("refresh_expires_in")
                )
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
        request_id = str(uuid4())

        async def send(token: str) -> dict[str, object]:
            headers = {
                "Authorization": f"Bearer {token}",
                "X-Telegram-User-Id": str(telegram_user_id),
                "X-Request-ID": request_id,
            }
            if idempotency_key is not None:
                headers["Idempotency-Key"] = idempotency_key
            status_code: int | None = None
            response_status_code: int | None = None
            failed = False
            result: dict[str, object] | None = None
            try:
                request_kwargs = {"headers": headers}
                if payload is not None:
                    request_kwargs["json"] = payload
                response = await self._client.request(
                    method, path.lstrip("/"), **request_kwargs
                )
                response_status_code = response.status_code
                response.raise_for_status()
                result = response.json()
            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code
            except (httpx.HTTPError, ValueError):
                failed = True
            outcome = (
                "http_error"
                if status_code is not None and status_code >= 400
                else "request_failed"
                if failed
                else "invalid_response"
                if not isinstance(result, dict)
                else "success"
            )
            self._log_request(request_id, method, path, response_status_code or status_code, outcome)
            if status_code is not None:
                raise HubClientError(
                    f"Hub rejected the request (HTTP {status_code})",
                    status_code=status_code,
                )
            if failed:
                raise HubClientError("Hub API request failed")
            if not isinstance(result, dict):
                raise HubClientError("Hub returned an invalid response")
            return result

        token = await self._access_token(telegram_user_id)
        try:
            return await send(token)
        except HubClientError as exc:
            if exc.status_code != 401:
                raise
            try:
                credentials = self._store.load(self.client_id)
                if credentials is None:
                    raise HubNotPairedError("Hub pairing is no longer available")
                # Another concurrent request may already have refreshed this
                # credential. Do not expire the newer token or rotate twice.
                if credentials.get("access_token") == token:
                    credentials["access_expires_at"] = (
                        datetime.now(UTC) - timedelta(seconds=1)
                    ).isoformat()
                    self._store.save(self.client_id, credentials)
            except HubClientError:
                raise
            except Exception as store_exc:
                raise HubClientError("Could not refresh credentials from the OS vault") from store_exc
            refreshed_token = await self._access_token(telegram_user_id)
            return await send(refreshed_token)


    async def _authorized_download(
        self,
        path: str,
        telegram_user_id: int,
        *,
        max_bytes: int,
    ) -> bytes:
        request_id = str(uuid4())

        async def send(token: str) -> bytes:
            headers = {
                "Authorization": f"Bearer {token}",
                "X-Telegram-User-Id": str(telegram_user_id),
                "X-Request-ID": request_id,
            }
            response_status_code: int | None = None
            try:
                async with self._client.stream(
                    "GET", path.lstrip("/"), headers=headers
                ) as response:
                    response_status_code = response.status_code
                    response.raise_for_status()
                    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                    if content_type != "video/mp4":
                        self._log_request(request_id, "GET", path, response_status_code, "invalid_response")
                        raise HubClientError("Hub returned an invalid video response", status_code=502)
                    content_length = response.headers.get("content-length")
                    if content_length is not None:
                        if not content_length.isdecimal():
                            self._log_request(request_id, "GET", path, response_status_code, "invalid_response")
                            raise HubClientError("Hub returned an invalid video size", status_code=502)
                        if int(content_length) > max_bytes:
                            self._log_request(request_id, "GET", path, response_status_code, "http_error")
                            raise HubClientError("Video exceeds the Telegram upload limit", status_code=413)
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(data) + len(chunk) > max_bytes:
                            self._log_request(request_id, "GET", path, response_status_code, "http_error")
                            raise HubClientError("Video exceeds the Telegram upload limit", status_code=413)
                        data.extend(chunk)
            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code
                self._log_request(request_id, "GET", path, status_code, "http_error")
                raise HubClientError(
                    f"Hub rejected the request (HTTP {status_code})",
                    status_code=status_code,
                ) from None
            except HubClientError:
                raise
            except httpx.HTTPError:
                self._log_request(request_id, "GET", path, response_status_code, "request_failed")
                raise HubClientError("Hub video download failed") from None

            if len(data) < 12 or data[4:8] != b"ftyp":
                self._log_request(request_id, "GET", path, response_status_code, "invalid_response")
                raise HubClientError("Hub returned an invalid MP4 file", status_code=502)
            self._log_request(request_id, "GET", path, response_status_code, "success")
            return bytes(data)

        token = await self._access_token(telegram_user_id)
        try:
            return await send(token)
        except HubClientError as exc:
            if exc.status_code != 401:
                raise
            try:
                credentials = self._store.load(self.client_id)
                if credentials is None:
                    raise HubNotPairedError("Hub pairing is no longer available")
                if credentials.get("access_token") == token:
                    credentials["access_expires_at"] = (
                        datetime.now(UTC) - timedelta(seconds=1)
                    ).isoformat()
                    self._store.save(self.client_id, credentials)
            except HubClientError:
                raise
            except Exception as store_exc:
                raise HubClientError("Could not refresh credentials from the OS vault") from store_exc
            refreshed_token = await self._access_token(telegram_user_id)
            return await send(refreshed_token)

    async def status(self, telegram_user_id: int) -> dict[str, object]:
        return await self._authorized(
            "GET", "/integrations/telegram/status", telegram_user_id
        )

    async def get_task(self, telegram_user_id: int, task_id: str) -> dict[str, object]:
        """Fetch one task through the paired client's existing tasks:read scope."""
        canonical_id = str(UUID(task_id))
        return await self._authorized(
            "GET",
            f"/integrations/telegram/tasks/{canonical_id}",
            telegram_user_id,
        )

    async def get_task_result(self, telegram_user_id: int, task_id: str) -> dict[str, object]:
        """Fetch the bounded terminal result for this paired client's own task."""
        canonical_id = str(UUID(task_id))
        return await self._authorized(
            "GET",
            f"/integrations/telegram/tasks/{canonical_id}/result",
            telegram_user_id,
        )

    async def cancel_task(self, telegram_user_id: int, task_id: str) -> dict[str, object]:
        """Cancel only a queued or approval-waiting task owned by this paired client."""
        canonical_id = str(UUID(task_id))
        return await self._authorized(
            "POST",
            f"/integrations/telegram/tasks/{canonical_id}/cancel",
            telegram_user_id,
            {},
        )

    async def list_tasks(
        self,
        telegram_user_id: int,
        *,
        cursor: str | None = None,
        limit: int = 20,
        status: str | None = None,
    ) -> dict[str, object]:
        """List one bounded, optionally status-filtered page for this paired client."""
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
            raise ValueError("Task page limit must be between 1 and 50")
        params: dict[str, str | int] = {"limit": limit}
        if status is not None:
            allowed_statuses = {
                "queued", "running", "waiting_approval", "succeeded",
                "failed", "cancelled", "rejected",
            }
            if not isinstance(status, str) or status not in allowed_statuses:
                raise ValueError("Invalid task status filter")
            params["status"] = status
        if cursor is not None:
            if (
                not isinstance(cursor, str)
                or not cursor
                or len(cursor) > 512
                or not cursor.isascii()
                or not cursor.replace("_", "").replace("-", "").isalnum()
            ):
                raise ValueError("Invalid task page cursor")
            params["cursor"] = cursor
        return await self._authorized(
            "GET",
            f"/integrations/telegram/tasks?{urlencode(params)}",
            telegram_user_id,
        )

    async def get_task_history(
        self,
        telegram_user_id: int,
        task_id: str,
        *,
        limit: int = 20,
    ) -> dict[str, object]:
        """Fetch the bounded audit timeline for this paired client's own task."""
        canonical_id = str(UUID(task_id))
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
            raise ValueError("Task history limit must be between 1 and 50")
        return await self._authorized(
            "GET",
            f"/integrations/telegram/tasks/{canonical_id}/events?{urlencode({'limit': limit})}",
            telegram_user_id,
        )

    async def list_video_providers(self, telegram_user_id: int) -> dict[str, object]:
        """Read the non-secret video provider catalog through video:read."""
        return await self._authorized(
            "GET", "/integrations/telegram/video/providers", telegram_user_id
        )

    async def preview_video_route(
        self,
        telegram_user_id: int,
        *,
        duration_seconds: int,
        aspect_ratio: str,
        resolution: str = "720p",
        remote_processing_approved: bool = False,
        remote_spend_approved: bool = False,
        max_remote_cost_usd: float = 0.0,
    ) -> dict[str, object]:
        """Request a read-only local-first route preview through video:read."""
        if (
            isinstance(duration_seconds, bool)
            or not isinstance(duration_seconds, int)
            or not 3 <= duration_seconds <= 60
        ):
            raise ValueError("Video route preview duration must be between 3 and 60 seconds")
        if not isinstance(aspect_ratio, str) or aspect_ratio not in {"9:16", "16:9", "1:1", "4:5"}:
            raise ValueError("Unsupported video route preview aspect ratio")
        if not isinstance(resolution, str) or resolution not in {"720p", "1080p"}:
            raise ValueError("Unsupported video route preview resolution")
        if (
            not isinstance(remote_processing_approved, bool)
            or not isinstance(remote_spend_approved, bool)
        ):
            raise ValueError("Remote consent values must be booleans")
        if (
            isinstance(max_remote_cost_usd, bool)
            or not isinstance(max_remote_cost_usd, (int, float))
            or not math.isfinite(float(max_remote_cost_usd))
            or not 0 <= float(max_remote_cost_usd) <= 10_000
        ):
            raise ValueError("Remote cost cap must be finite and between 0 and 10000 USD")
        return await self._authorized(
            "POST",
            "/integrations/telegram/video/route-preview",
            telegram_user_id,
            {
                "duration_seconds": duration_seconds,
                "aspect_ratio": aspect_ratio,
                "resolution": resolution,
                "remote_processing_approved": remote_processing_approved,
                "remote_spend_approved": remote_spend_approved,
                "max_remote_cost_usd": float(max_remote_cost_usd),
            },
        )

    async def list_video_generations(
        self,
        telegram_user_id: int,
        *,
        cursor: str | None = None,
        limit: int = 10,
    ) -> dict[str, object]:
        """List one bounded owner-scoped video generation page."""
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20:
            raise ValueError("Video generation page limit must be between 1 and 20")
        params: dict[str, str | int] = {"limit": limit}
        if cursor is not None:
            if (
                not isinstance(cursor, str)
                or not cursor
                or len(cursor) > 512
                or not cursor.isascii()
                or not cursor.replace("_", "").replace("-", "").isalnum()
            ):
                raise ValueError("Invalid video generation page cursor")
            params["cursor"] = cursor
        return await self._authorized(
            "GET",
            f"/integrations/telegram/video/generations?{urlencode(params)}",
            telegram_user_id,
        )

    async def get_video_generation(
        self, telegram_user_id: int, generation_id: str
    ) -> dict[str, object]:
        """Fetch one generation status through the paired owner's video:read scope."""
        canonical_id = str(UUID(generation_id))
        return await self._authorized(
            "GET",
            f"/integrations/telegram/video/generations/{canonical_id}",
            telegram_user_id,
        )

    async def cancel_video_generation(
        self, telegram_user_id: int, generation_id: str
    ) -> dict[str, object]:
        """Cancel only prepared or queued generation work before provider submission."""
        canonical_id = str(UUID(generation_id))
        return await self._authorized(
            "DELETE",
            f"/integrations/telegram/video/generations/{canonical_id}",
            telegram_user_id,
        )

    async def list_video_render_drafts(
        self, telegram_user_id: int, analysis_id: str
    ) -> dict[str, object]:
        """List bounded owner-scoped prepared timeline drafts through video:read."""
        canonical_id = str(UUID(analysis_id))
        return await self._authorized(
            "GET",
            f"/integrations/telegram/video/render-drafts?{urlencode({'analysis_id': canonical_id})}",
            telegram_user_id,
        )

    async def submit_video_render(
        self,
        telegram_user_id: int,
        draft_id: str,
        *,
        idempotency_key: str,
    ) -> dict[str, object]:
        """Submit a local deterministic FFmpeg render through the explicit video:render scope."""
        canonical_id = str(UUID(draft_id))
        if (
            not isinstance(idempotency_key, str)
            or not idempotency_key.strip()
            or len(idempotency_key) > 255
            or "\r" in idempotency_key
            or "\n" in idempotency_key
        ):
            raise ValueError("Video render idempotency key must contain 1 to 255 safe characters")
        return await self._authorized(
            "POST",
            "/integrations/telegram/video/renders",
            telegram_user_id,
            {"draft_id": canonical_id},
            idempotency_key=idempotency_key.strip(),
        )

    async def get_video_render(
        self, telegram_user_id: int, render_id: str
    ) -> dict[str, object]:
        """Read one owner-scoped deterministic render through video:read."""
        canonical_id = str(UUID(render_id))
        return await self._authorized(
            "GET",
            f"/integrations/telegram/video/renders/{canonical_id}",
            telegram_user_id,
        )


    async def list_video_renders(
        self,
        telegram_user_id: int,
        *,
        limit: int = 10,
        cursor: str | None = None,
    ) -> dict[str, object]:
        """List one bounded owner-scoped page of deterministic local renders."""
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20:
            raise ValueError("Video render page limit must be between 1 and 20")
        params: dict[str, str | int] = {"limit": limit}
        if cursor is not None:
            if (
                not isinstance(cursor, str)
                or not cursor
                or len(cursor) > 512
                or not cursor.isascii()
                or not cursor.replace("_", "").replace("-", "").isalnum()
            ):
                raise ValueError("Invalid video render page cursor")
            params["cursor"] = cursor
        return await self._authorized(
            "GET",
            f"/integrations/telegram/video/renders?{urlencode(params)}",
            telegram_user_id,
        )

    async def download_video_render(
        self, telegram_user_id: int, render_id: str
    ) -> bytes:
        """Download one completed owner-scoped MP4 for explicit Telegram delivery."""
        canonical_id = str(UUID(render_id))
        return await self._authorized_download(
            f"/integrations/telegram/video/renders/{canonical_id}/file",
            telegram_user_id,
            max_bytes=MAX_TELEGRAM_VIDEO_BYTES,
        )

    async def cancel_video_render(
        self, telegram_user_id: int, render_id: str
    ) -> dict[str, object]:
        """Request cancellation of one owner-scoped local render through video:cancel."""
        canonical_id = str(UUID(render_id))
        return await self._authorized(
            "DELETE",
            f"/integrations/telegram/video/renders/{canonical_id}",
            telegram_user_id,
        )

    async def search_memory(self, telegram_user_id: int, query: str) -> dict[str, object]:
        return await self._authorized(
            "POST",
            "/integrations/telegram/memory/search",
            telegram_user_id,
            {"query": query, "limit": 5},
        )

    async def preview_memory_dream(
        self, telegram_user_id: int, *, limit: int = 100, similarity_threshold: float = 0.82
    ) -> dict[str, object]:
        """Request a read-only owner-scoped memory consolidation preview."""
        if not 1 <= limit <= 100:
            raise ValueError("Memory review limit must be between 1 and 100")
        if not 0.75 <= similarity_threshold <= 1.0:
            raise ValueError("Memory similarity threshold must be between 0.75 and 1.0")
        return await self._authorized(
            "POST",
            "/integrations/telegram/memory/dream/preview",
            telegram_user_id,
            {"limit": limit, "similarity_threshold": similarity_threshold},
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
