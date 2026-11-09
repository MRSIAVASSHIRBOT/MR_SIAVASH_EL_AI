"""Validated environment configuration with fail-closed defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass


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


@dataclass(frozen=True, slots=True)
class Settings:
    telegram_bot_token: str
    owner_ids: frozenset[int]
    public_enabled: bool = False
    environment: str = "development"
    poll_timeout_seconds: int = 25
    request_timeout_seconds: int = 35
    rate_limit_per_minute: int = 12
    hub_api_base_url: str | None = None
    hub_scoped_token: str | None = None
    hub_integration_enabled: bool = False
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
        hub_url = os.getenv("HUB_API_BASE_URL", "").strip() or None
        hub_token = os.getenv("HUB_SCOPED_TOKEN", "").strip() or None
        if hub_enabled:
            raise ConfigurationError(
                "Hub integration is not enabled in this release; owner pairing is not implemented"
            )
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
            hub_api_base_url=hub_url,
            hub_scoped_token=hub_token,
            hub_integration_enabled=False,
            site_public_api_base_url=os.getenv("SITE_PUBLIC_API_BASE_URL", "").strip() or None,
        )
