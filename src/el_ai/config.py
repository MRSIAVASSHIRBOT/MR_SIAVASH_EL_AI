"""Strict runtime configuration with secret-safe representations."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field


class ConfigurationError(ValueError):
    """Raised when required runtime configuration is missing or invalid."""


def _parse_bool(name: str, value: str | None, default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{name} must be a boolean value.")


def _parse_owner_ids(value: str | None) -> frozenset[int]:
    if value is None or not value.strip():
        raise ConfigurationError("TELEGRAM_OWNER_IDS must contain at least one numeric user ID.")
    owner_ids: set[int] = set()
    for item in value.split(","):
        candidate = item.strip()
        if not candidate.isdecimal():
            raise ConfigurationError("TELEGRAM_OWNER_IDS contains a non-numeric value.")
        owner_id = int(candidate)
        if owner_id <= 0:
            raise ConfigurationError("TELEGRAM_OWNER_IDS values must be positive.")
        owner_ids.add(owner_id)
    if not owner_ids:
        raise ConfigurationError("TELEGRAM_OWNER_IDS must contain at least one numeric user ID.")
    return frozenset(owner_ids)


def _parse_int(name: str, value: str | None, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        raise ConfigurationError(f"{name} must be an integer.") from None


def _parse_float(name: str, value: str | None, default: float) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        raise ConfigurationError(f"{name} must be a number.") from None


@dataclass(frozen=True, slots=True)
class Settings:
    telegram_bot_token: str = field(repr=False)
    owner_ids: frozenset[int]
    public_enabled: bool
    environment: str
    poll_timeout_seconds: int
    request_timeout_seconds: float

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        source = os.environ if environ is None else environ
        token = source.get("TELEGRAM_BOT_TOKEN", "").strip()
        if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]{20,}", token):
            raise ConfigurationError("TELEGRAM_BOT_TOKEN is missing or malformed.")

        owner_ids = _parse_owner_ids(source.get("TELEGRAM_OWNER_IDS"))
        public_enabled = _parse_bool(
            "TELEGRAM_PUBLIC_ENABLED",
            source.get("TELEGRAM_PUBLIC_ENABLED"),
            False,
        )
        environment = source.get("EL_AI_ENVIRONMENT", "development").strip().lower()
        if environment not in {"development", "test", "production"}:
            raise ConfigurationError("EL_AI_ENVIRONMENT must be development, test or production.")

        poll_timeout = _parse_int(
            "TELEGRAM_POLL_TIMEOUT_SECONDS",
            source.get("TELEGRAM_POLL_TIMEOUT_SECONDS"),
            25,
        )
        request_timeout = _parse_float(
            "TELEGRAM_REQUEST_TIMEOUT_SECONDS",
            source.get("TELEGRAM_REQUEST_TIMEOUT_SECONDS"),
            35.0,
        )
        if not 1 <= poll_timeout <= 50:
            raise ConfigurationError("TELEGRAM_POLL_TIMEOUT_SECONDS must be between 1 and 50.")
        if not 5 <= request_timeout <= 90:
            raise ConfigurationError("TELEGRAM_REQUEST_TIMEOUT_SECONDS must be between 5 and 90.")
        if request_timeout <= poll_timeout:
            raise ConfigurationError(
                "TELEGRAM_REQUEST_TIMEOUT_SECONDS must exceed the polling timeout."
            )

        return cls(
            telegram_bot_token=token,
            owner_ids=owner_ids,
            public_enabled=public_enabled,
            environment=environment,
            poll_timeout_seconds=poll_timeout,
            request_timeout_seconds=request_timeout,
        )
