import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_ROOT = ROOT / "contracts" / "telegram-business" / "v1"


def test_telegram_business_manifest_declares_supported_update_variants():
    manifest = json.loads((CONTRACT_ROOT / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["contract_version"] == "telegram-business-event.v1"
    assert manifest["owner"] == "MR_SIAVASH_EL_AI"
    assert set(manifest["supported_event_types"]) == {
        "business_connection",
        "business_message",
        "edited_business_message",
        "deleted_business_messages",
    }


def test_telegram_business_contract_keeps_owner_authority_separate():
    manifest = json.loads((CONTRACT_ROOT / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["actor_model"]["owner_privileges_inherited"] is False
    assert manifest["actor_model"]["hub_scopes_inherited"] is False
    assert manifest["rights_policy"]["reply_requires"] == "can_reply"
    assert manifest["rights_policy"]["connection_must_be_enabled"] is True
    assert manifest["rights_policy"]["message_read_requires"] == "can_read_messages"
    assert manifest["actor_model"]["default_business_capabilities"] == []
    assert manifest["runtime_status"]["persistent_connection_registry"] == "implemented_local_owner_bound_registry"
    assert manifest["runtime_status"]["business_event_processor"] == "implemented_tested_not_wired"
    assert manifest["runtime_status"]["business_runtime_handler"] == "wired_local_polling_processor"
    assert manifest["runtime_status"]["telegram_business_subscription"] == "disabled_allowed_updates_message_only"
    assert manifest["runtime_status"]["business_reply_execution"] == "not_implemented"


def test_telegram_business_json_schema_is_versioned_and_bounded():
    schema = json.loads(
        (CONTRACT_ROOT / "update.schema.json").read_text(encoding="utf-8")
    )

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert len(schema["oneOf"]) == 3
    assert schema["$defs"]["message"]["required"] == [
        "message_id",
        "date",
        "chat",
        "sender",
    ]
    assert schema["$defs"]["deletedBusinessMessages"]["properties"]["message_ids"][
        "maxItems"
    ] == 1000
    assert "file_id" not in json.dumps(schema)
    assert "file_unique_id" not in json.dumps(schema)
    assert schema["$defs"]["message"]["properties"]["is_from_business_account"] == {"type": "boolean"}
