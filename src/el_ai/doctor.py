"""Read-only local preflight for the EL_AI Telegram and Hub runtime."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from el_ai.config import (
    ConfigurationError,
    _boolean,
    _hub_url,
    _owner_ids,
    _positive_int,
    _public_url,
    _telegram_bot_token,
)
from el_ai.integrations.secrets import KeyringCredentialStore, SecretStoreError, build_credential_store


CheckStatus = Literal["ok", "warning", "error", "disabled"]


@dataclass(frozen=True, slots=True)
class DoctorCheck:
    name: str
    status: CheckStatus
    detail: str


def _state_directory_check() -> DoctorCheck:
    path = Path(os.getenv("EL_AI_STATE_DIR", "./data")).expanduser()
    try:
        if path.exists():
            if not path.is_dir():
                return DoctorCheck(
                    "state_directory", "error", "configured state path is not a directory"
                )
            if not os.access(path, os.W_OK):
                return DoctorCheck(
                    "state_directory", "error", "state directory is not writable"
                )
            return DoctorCheck("state_directory", "ok", "existing state directory is writable")
        parent = path.parent
        if parent.exists() and parent.is_dir() and os.access(parent, os.W_OK):
            return DoctorCheck(
                "state_directory",
                "warning",
                "directory does not exist yet; first runtime start may create it",
            )
        return DoctorCheck(
            "state_directory", "error", "state directory parent is missing or not writable"
        )
    except OSError:
        return DoctorCheck("state_directory", "error", "state directory cannot be inspected")


def collect_report() -> dict[str, object]:
    """Collect configuration and local capability checks without network or writes."""
    checks: list[DoctorCheck] = []

    transport = os.getenv("TELEGRAM_TRANSPORT", "local_polling").strip().lower()
    if transport in {"local_polling", "cloudflare_bridge"}:
        checks.append(DoctorCheck("telegram_transport", "ok", "transport mode is supported"))
    else:
        checks.append(
            DoctorCheck(
                "telegram_transport",
                "error",
                "TELEGRAM_TRANSPORT must be local_polling or cloudflare_bridge",
            )
        )

    if transport == "local_polling":
        raw_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        try:
            _telegram_bot_token(raw_token)
        except ConfigurationError:
            detail = (
                "TELEGRAM_BOT_TOKEN is required; no value is displayed"
                if not raw_token.strip()
                else "TELEGRAM_BOT_TOKEN format looks invalid; value is redacted and not verified remotely"
            )
            checks.append(DoctorCheck("telegram_bot_token", "error", detail))
        else:
            checks.append(
                DoctorCheck(
                    "telegram_bot_token",
                    "ok",
                    "configured; token value is redacted and Telegram verification was not attempted",
                )
            )
    elif transport == "cloudflare_bridge":
        checks.append(
            DoctorCheck(
                "telegram_bot_token",
                "disabled",
                "not required; Telegram ingress and delivery are handled by the Cloudflare Worker",
            )
        )
        try:
            _public_url(os.getenv("TELEGRAM_BRIDGE_URL"), "", "TELEGRAM_BRIDGE_URL")
            checks.append(
                DoctorCheck("telegram_bridge_url", "ok", "bridge URL is valid under HTTPS policy")
            )
        except ConfigurationError:
            checks.append(
                DoctorCheck("telegram_bridge_url", "error", "TELEGRAM_BRIDGE_URL must be a clean HTTPS URL")
            )
        bridge_secret = os.getenv("TELEGRAM_BRIDGE_SHARED_SECRET", "")
        if len(bridge_secret) >= 32:
            checks.append(
                DoctorCheck(
                    "telegram_bridge_secret",
                    "ok",
                    "configured with the minimum supported length; value is redacted",
                )
            )
        else:
            checks.append(
                DoctorCheck(
                    "telegram_bridge_secret",
                    "error",
                    "TELEGRAM_BRIDGE_SHARED_SECRET must contain at least 32 characters; value is redacted",
                )
            )
        try:
            _positive_int(
                "TELEGRAM_BRIDGE_TIMEOUT_SECONDS", 8, minimum=3, maximum=30
            )
            checks.append(
                DoctorCheck("telegram_bridge_timeout", "ok", "value is within the supported range")
            )
        except ConfigurationError:
            checks.append(
                DoctorCheck("telegram_bridge_timeout", "error", "value must be between 3 and 30")
            )
    else:
        checks.append(
            DoctorCheck("telegram_bot_token", "disabled", "not checked because transport mode is invalid")
        )

    try:
        owners = _owner_ids(os.getenv("TELEGRAM_OWNER_IDS"))
        if owners:
            checks.append(
                DoctorCheck("telegram_owner_ids", "ok", "one or more owner IDs are configured")
            )
        else:
            checks.append(
                DoctorCheck(
                    "telegram_owner_ids",
                    "error",
                    "at least one owner ID is required for owner-only controls",
                )
            )
    except ConfigurationError:
        checks.append(
            DoctorCheck(
                "telegram_owner_ids", "error", "owner IDs must be positive numeric values"
            )
        )

    environment = os.getenv("EL_AI_ENVIRONMENT", "development").strip().lower()
    if environment in {"development", "test", "production"}:
        checks.append(DoctorCheck("environment", "ok", "environment value is supported"))
    else:
        checks.append(
            DoctorCheck("environment", "error", "environment must be development, test, or production")
        )

    for name, default in (
        ("TELEGRAM_PUBLIC_ENABLED", False),
        ("HUB_INTEGRATION_ENABLED", False),
    ):
        try:
            value = _boolean(name, default)
            status: CheckStatus = "ok" if name == "TELEGRAM_PUBLIC_ENABLED" else (
                "ok" if value else "disabled"
            )
            detail = (
                "public access flag is valid"
                if name == "TELEGRAM_PUBLIC_ENABLED"
                else "Hub integration is enabled"
                if value
                else "Hub integration is disabled by configuration"
            )
            check_name = (
                "telegram_public_access"
                if name == "TELEGRAM_PUBLIC_ENABLED"
                else "hub_integration"
            )
            checks.append(DoctorCheck(check_name, status, detail))
        except ConfigurationError:
            check_name = (
                "telegram_public_access"
                if name == "TELEGRAM_PUBLIC_ENABLED"
                else "hub_integration"
            )
            checks.append(DoctorCheck(check_name, "error", "must be a boolean value"))

    hub_enabled = False
    try:
        hub_enabled = _boolean("HUB_INTEGRATION_ENABLED", False)
    except ConfigurationError:
        pass

    if transport == "cloudflare_bridge" and not hub_enabled:
        checks.append(
            DoctorCheck(
                "cloudflare_bridge_hub_requirement",
                "error",
                "HUB_INTEGRATION_ENABLED=true is required for Cloudflare bridge mode",
            )
        )

    if hub_enabled:
        try:
            allow_remote = _boolean("HUB_ALLOW_REMOTE", False)
            hub_url = _hub_url(os.getenv("HUB_API_BASE_URL"), allow_remote=allow_remote)
            if hub_url is None:
                checks.append(
                    DoctorCheck("hub_api_url", "error", "HUB_API_BASE_URL is required")
                )
            else:
                checks.append(
                    DoctorCheck(
                        "hub_api_url",
                        "ok",
                        "Hub URL is valid under the loopback/HTTPS policy",
                    )
                )
        except ConfigurationError:
            checks.append(
                DoctorCheck(
                    "hub_api_url",
                    "error",
                    "Hub URL or remote-access policy is invalid",
                )
            )
        try:
            store_mode = os.getenv("EL_AI_CREDENTIAL_STORE", "keyring").strip().lower()
            if store_mode == "keyring":
                KeyringCredentialStore()
            else:
                build_credential_store(
                    Path(os.getenv("EL_AI_STATE_DIR", "./data")).expanduser()
                )
            checks.append(
                DoctorCheck(
                    "hub_credential_store",
                    "ok",
                    "the configured protected credential backend is available",
                )
            )
        except SecretStoreError:
            checks.append(
                DoctorCheck(
                    "hub_credential_store",
                    "error",
                    "no trusted operating-system credential backend is available",
                )
            )
    else:
        checks.append(
            DoctorCheck(
                "hub_api_url",
                "disabled",
                "not checked because Hub integration is disabled",
            )
        )
        checks.append(
            DoctorCheck(
                "hub_credential_store",
                "disabled",
                "not accessed because Hub integration is disabled",
            )
        )

    for name, default, label in (
        (
            "SITE_PUBLIC_API_BASE_URL",
            "https://mr-siavash-ai-gateway.mrsiavashirbot.workers.dev",
            "public API Gateway URL",
        ),
        (
            "SITE_PUBLIC_WEB_BASE_URL",
            "https://mrsiavasshirbot.github.io/MR_SIAVASH_AI",
            "public website URL",
        ),
    ):
        try:
            _public_url(os.getenv(name), default, name)
            checks.append(DoctorCheck(name.lower(), "ok", f"{label} is valid"))
        except ConfigurationError:
            checks.append(DoctorCheck(name.lower(), "error", f"{label} must be a clean HTTP(S) URL"))

    runtime_limits = [
        ("TELEGRAM_REQUEST_TIMEOUT_SECONDS", 35, 5, 60),
        ("TELEGRAM_RATE_LIMIT_PER_MINUTE", 12, 1, 60),
    ]
    if transport == "local_polling":
        runtime_limits.append(("TELEGRAM_POLL_TIMEOUT_SECONDS", 25, 1, 50))
    for name, default, minimum, maximum in runtime_limits:
        try:
            _positive_int(name, default, minimum=minimum, maximum=maximum)
            checks.append(DoctorCheck(name.lower(), "ok", "value is within the supported range"))
        except ConfigurationError:
            checks.append(
                DoctorCheck(name.lower(), "error", "value is outside the supported range")
            )

    checks.append(_state_directory_check())
    ready = not any(check.status == "error" for check in checks)
    return {
        "ready": ready,
        "mode": "read_only_offline",
        "network_requests": 0,
        "filesystem_writes": 0,
        "secrets_displayed": False,
        "checks": [asdict(check) for check in checks],
    }


def render_json(report: dict[str, object]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)


def render_text(report: dict[str, object]) -> str:
    labels = {
        "ok": "OK",
        "warning": "WARN",
        "error": "ERROR",
        "disabled": "DISABLED",
    }
    lines = [
        "EL_AI preflight — read-only / offline; no secrets are displayed",
        f"Readiness: {'READY' if report['ready'] else 'BLOCKED'}",
    ]
    for item in report["checks"]:
        check = item
        lines.append(
            f"[{labels[check['status']]}] {check['name']}: {check['detail']}"
        )
    lines.append("Network requests: 0 | Filesystem writes: 0 | Secrets displayed: no")
    return "\n".join(lines)


def main(*, json_output: bool = False) -> int:
    report = collect_report()
    print(render_json(report) if json_output else render_text(report))
    return 0 if report["ready"] else 1
