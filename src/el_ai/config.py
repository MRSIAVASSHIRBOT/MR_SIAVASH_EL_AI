"""Validated environment configuration with fail-closed defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


class ConfigurationError(ValueError):
    """Raised when runtime configuration is missing or invalid."""


def _boolean(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{name} must be a boolean value")


def _positive_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    try:
        value = default if raw is None else int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ConfigurationError(f"{name} must be between {minimum} and {maximum}")
    return value


def _owner_ids(raw: str | None) -> frozenset[int]:
    if not raw or not raw.strip():
        return frozenset()
    values: set[int] = set()
    for item in raw.split(","):
        candidate = item.strip()
        if not candidate.isdecimal() or int(candidate) <= 0:
            raise ConfigurationError("TELEGRAM_OWNER_IDS must contain positive numeric IDs")
        values.add(int(candidate))
    return frozenset(values)


def _hub_url(raw: str | None, *, allow_remote: bool = False) -> str | None:
    if not raw or not raw.strip():
        return None
    value = raw.strip()
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ConfigurationError("HUB_API_BASE_URL must be a clean HTTP(S) base URL")
    try:
        parsed.port
    except ValueError as exc:
        raise ConfigurationError("HUB_API_BASE_URL contains an invalid port") from exc
    is_loopback = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if not is_loopback and (parsed.scheme != "https" or not allow_remote):
        raise ConfigurationError(
            "Remote Hub URLs require HTTPS and explicit HUB_ALLOW_REMOTE=true"
        )
    return value.rstrip("/")


@dataclass(frozen=True, slots=True)
class Settings:
    telegram_bot_token: str = field(repr=False)
    owner_ids: frozenset[int]
    public_enabled: bool = False
    environment: str = "development"
    poll_timeout_seconds: int = 25
    request_timeout_seconds: int = 35
    rate_limit_per_minute: int = 12
    state_dir: Path = Path("./data")
    hub_api_base_url: str | None = None
    hub_integration_enabled: bool = False
    hub_allow_remote: bool = False
    site_public_api_base_url: str | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        if not token:
            raise ConfigurationError("TELEGRAM_BOT_TOKEN is required")
        environment = os.getenv("EL_AI_ENVIRONMENT", "development").strip().lower()
        if environment not in {"development", "test", "production"}:
            raise ConfigurationError("EL_AI_ENVIRONMENT must be development, test, or production")
        hub_enabled = _boolean("HUB_INTEGRATION_ENABLED", False)
        hub_allow_remote = _boolean("HUB_ALLOW_REMOTE", False)
        hub_url = _hub_url(os.getenv("HUB_API_BASE_URL"), allow_remote=hub_allow_remote)
        if hub_enabled and hub_url is None:
            raise ConfigurationError("HUB_API_BASE_URL is required when Hub integration is enabled")
        return cls(
            telegram_bot_token=token,
            owner_ids=_owner_ids(os.getenv("TELEGRAM_OWNER_IDS")),
            public_enabled=_boolean("TELEGRAM_PUBLIC_ENABLED", False),
            environment=environment,
            poll_timeout_seconds=_positive_int(
                "TELEGRAM_POLL_TIMEOUT_SECONDS", 25, minimum=1, maximum=50
            ),
            request_timeout_seconds=_positive_int(
                "TELEGRAM_REQUEST_TIMEOUT_SECONDS", 35, minimum=5, maximum=60
            ),
            rate_limit_per_minute=_positive_int(
                "TELEGRAM_RATE_LIMIT_PER_MINUTE", 12, minimum=1, maximum=60
            ),
            state_dir=Path(os.getenv("EL_AI_STATE_DIR", "./data")).expanduser(),
            hub_api_base_url=hub_url,
            hub_integration_enabled=hub_enabled,
            hub_allow_remote=hub_allow_remote,
            site_public_api_base_url=os.getenv("SITE_PUBLIC_API_BASE_URL", "").strip() or None,
        )
