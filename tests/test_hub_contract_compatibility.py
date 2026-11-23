"""Compatibility checks against the pinned canonical Hub Telegram contract."""

from __future__ import annotations

import json
from pathlib import Path

from el_ai.integrations.hub import HubClient, _STATIC_HUB_ROUTES


_MANIFEST_PATH = (
    Path(__file__).resolve().parent / "fixtures" / "hub-hybrid-manifest-v1.json"
)
_PATH_PARAMETERS = {
    "client_id": "11111111-1111-4111-8111-111111111111",
    "task_id": "22222222-2222-4222-8222-222222222222",
    "memory_id": "33333333-3333-4333-8333-333333333333",
    "generation_id": "44444444-4444-4444-8444-444444444444",
    "render_id": "55555555-5555-4555-8555-555555555555",
    "job_id": "66666666-6666-4666-8666-666666666666",
}


def _contract() -> dict[str, object]:
    return json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))


def _concrete_path(template: str) -> str:
    path = template
    for name, value in _PATH_PARAMETERS.items():
        path = path.replace("{" + name + "}", value)
    assert "{" not in path and "}" not in path, (
        f"Contract route has an unrecognized path parameter: {template}"
    )
    return path


def test_pinned_hub_contract_is_the_approved_v1_manifest() -> None:
    manifest = _contract()

    assert manifest["contract_id"] == "mr-siavash-hybrid-integration"
    assert manifest["contract_version"] == "1.0.0"
    assert manifest["status"] == "approved"


def test_all_paired_bearer_routes_have_declared_scopes_and_safe_log_templates() -> None:
    telegram = _contract()["hub_telegram"]
    scopes = set(telegram["scopes"])
    endpoints = [
        endpoint
        for endpoint in telegram["endpoints"]
        if endpoint["auth_mode"] == "paired_bearer"
    ]

    assert endpoints, "The canonical Hub contract must define paired-bearer endpoints"

    for endpoint in endpoints:
        route = endpoint["path"]
        scope = endpoint["required_scope"]
        assert scope in scopes, f"Undeclared scope for {endpoint['method']} {route}"

        concrete = _concrete_path(route)
        assert HubClient._route_template(concrete) == route, (
            f"HubClient observability does not map {endpoint['method']} {route} "
            "to its stable, identifier-free route template"
        )


def test_static_hub_route_templates_exist_in_the_paired_contract() -> None:
    endpoints = _contract()["hub_telegram"]["endpoints"]
    paired_paths = {
        endpoint["path"]
        for endpoint in endpoints
        if endpoint["auth_mode"] == "paired_bearer"
    }

    assert _STATIC_HUB_ROUTES <= paired_paths
