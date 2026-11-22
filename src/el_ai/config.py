"""Validated environment configuration with fail-closed defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


class ConfigurationError(ValueError):
    """Raised when runtime configuration is missing or invalid."""


def _telegram_bot_token(raw: str | None) -> str:
    """Validate the local Bot API token shape without contacting Telegram."""
    token = (raw or "").strip()
    if not token:
        raise ConfigurationError("TELEGRAM_BOT_TOKEN is required")
    bot_id, separator, secret = token.partition(":")
    if (
        not separator
        or not bot_id.isascii()
        or not bot_id.isdigit()
        or not secret
        or any(character.isspace() for character in token)
    ):
        raise ConfigurationError(
            "TELEGRAM_BOT_TOKEN must contain a numeric bot ID and a non-empty token"
        )
    return token


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


def _public_url(raw: str | None, default: str, name: str) -> str:
    value = (raw or default).strip()
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ConfigurationError(f"{name} must be a clean HTTP(S) base URL")
    try:
        parsed.port
    except ValueError as exc:
        raise ConfigurationError(f"{name} contains an invalid port") from exc
    if parsed.scheme != "https" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ConfigurationError(f"{name} must use HTTPS for remote hosts")
    return value.rstrip("/")


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
    telegram_bot_token: str | None = field(repr=False)
    owner_ids: frozenset[int]
    telegram_transport: str = "local_polling"
    telegram_bridge_url: str | None = None
    telegram_bridge_shared_secret: str | None = field(default=None, repr=False)
    telegram_bridge_timeout_seconds: int = 8
    public_enabled: bool = False
    environment: str = "development"
    poll_timeout_seconds: int = 25
    request_timeout_seconds: int = 35
    rate_limit_per_minute: int = 12
    state_dir: Path = Path("./data")
    hub_api_base_url: str | None = None
    hub_integration_enabled: bool = False
    hub_allow_remote: bool = False
    site_public_api_base_url: str = "https://mr-siavash-ai-gateway.mrsiavashirbot.workers.dev"
    site_public_web_base_url: str = "https://mrsiavasshirbot.github.io/MR_SIAVASH_AI"

    @classmethod
    def from_env(cls) -> "Settings":
        transport = os.getenv("TELEGRAM_TRANSPORT", "local_polling").strip().lower()
        if transport not in {"local_polling", "cloudflare_bridge"}:
            raise ConfigurationError(
                "TELEGRAM_TRANSPORT must be local_polling or cloudflare_bridge"
            )
        token = (
            _telegram_bot_token(os.getenv("TELEGRAM_BOT_TOKEN"))
            if transport == "local_polling"
            else None
        )
        bridge_url = None
        bridge_secret = None
        bridge_timeout = 8
        if transport == "cloudflare_bridge":
            bridge_timeout = _positive_int(
                "TELEGRAM_BRIDGE_TIMEOUT_SECONDS", 8, minimum=3, maximum=30
            )
            bridge_url = _public_url(
                os.getenv("TELEGRAM_BRIDGE_URL"), "", "TELEGRAM_BRIDGE_URL"
            )
            bridge_secret = os.getenv("TELEGRAM_BRIDGE_SHARED_SECRET", "")
            if len(bridge_secret) < 32:
                raise ConfigurationError(
                    "TELEGRAM_BRIDGE_SHARED_SECRET must contain at least 32 characters"
                )
        environment = os.getenv("EL_AI_ENVIRONMENT", "development").strip().lower()
        if environment not in {"development", "test", "production"}:
            raise ConfigurationError("EL_AI_ENVIRONMENT must be development, test, or production")
        hub_enabled = _boolean("HUB_INTEGRATION_ENABLED", False)
        hub_allow_remote = _boolean("HUB_ALLOW_REMOTE", False)
        hub_url = _hub_url(os.getenv("HUB_API_BASE_URL"), allow_remote=hub_allow_remote)
        if hub_enabled and hub_url is None:
            raise ConfigurationError("HUB_API_BASE_URL is required when Hub integration is enabled")
        owner_ids = _owner_ids(os.getenv("TELEGRAM_OWNER_IDS"))
        if transport == "cloudflare_bridge":
            if not owner_ids:
                raise ConfigurationError(
                    "TELEGRAM_OWNER_IDS is required for cloudflare_bridge transport"
                )
            if not hub_enabled:
                raise ConfigurationError(
                    "HUB_INTEGRATION_ENABLED=true is required for cloudflare_bridge transport"
                )
        return cls(
            telegram_bot_token=token,
            owner_ids=owner_ids,
            telegram_transport=transport,
            telegram_bridge_url=bridge_url,
            telegram_bridge_shared_secret=bridge_secret,
            telegram_bridge_timeout_seconds=bridge_timeout,
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
            site_public_api_base_url=_public_url(
                os.getenv("SITE_PUBLIC_API_BASE_URL"),
                "https://mr-siavash-ai-gateway.mrsiavashirbot.workers.dev",
                "SITE_PUBLIC_API_BASE_URL",
            ),
            site_public_web_base_url=_public_url(
                os.getenv("SITE_PUBLIC_WEB_BASE_URL"),
                "https://mrsiavasshirbot.github.io/MR_SIAVASH_AI",
                "SITE_PUBLIC_WEB_BASE_URL",
            ),
        )
