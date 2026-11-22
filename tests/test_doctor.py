import json

import pytest

from el_ai import doctor
from el_ai.integrations.secrets import SecretStoreError


def _base_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:abcdefghijklmnopqrstuvwxyz123456")
    monkeypatch.setenv("TELEGRAM_OWNER_IDS", "123456789")
    monkeypatch.setenv("EL_AI_ENVIRONMENT", "test")
    monkeypatch.setenv("EL_AI_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("HUB_INTEGRATION_ENABLED", "false")
    monkeypatch.setenv(
        "SITE_PUBLIC_API_BASE_URL",
        "https://mr-siavash-ai-gateway.mrsiavashirbot.workers.dev",
    )
    monkeypatch.setenv(
        "SITE_PUBLIC_WEB_BASE_URL",
        "https://mrsiavasshirbot.github.io/MR_SIAVASH_AI",
    )


def _check(report, name):
    return next(item for item in report["checks"] if item["name"] == name)


def test_doctor_reports_missing_bot_token_without_disclosing_any_secret(monkeypatch, tmp_path):
    _base_environment(monkeypatch, tmp_path)
    token = "123456:private-token-value"
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    report = doctor.collect_report()
    rendered = doctor.render_json(report)

    assert report["ready"] is False
    assert _check(report, "telegram_bot_token")["status"] == "error"
    assert token not in rendered
    assert "TELEGRAM_BOT_TOKEN" in rendered
    assert report["network_requests"] == 0
    assert report["secrets_displayed"] is False


def test_doctor_is_read_only_and_accepts_safe_local_configuration(monkeypatch, tmp_path):
    _base_environment(monkeypatch, tmp_path)
    state_dir = tmp_path / "state"

    report = doctor.collect_report()

    assert report["ready"] is True
    assert _check(report, "telegram_bot_token")["status"] == "ok"
    assert _check(report, "telegram_owner_ids")["status"] == "ok"
    assert _check(report, "hub_integration")["status"] == "disabled"
    assert _check(report, "state_directory")["status"] == "warning"
    assert report["network_requests"] == 0
    assert report["secrets_displayed"] is False
    assert not state_dir.exists()


def test_doctor_checks_keyring_only_when_hub_integration_is_enabled(
    monkeypatch, tmp_path
):
    _base_environment(monkeypatch, tmp_path)
    monkeypatch.setenv("HUB_INTEGRATION_ENABLED", "true")
    monkeypatch.setenv("HUB_API_BASE_URL", "http://127.0.0.1:8000")
    monkeypatch.setattr(doctor, "KeyringCredentialStore", lambda: object())

    report = doctor.collect_report()

    assert report["ready"] is True
    assert _check(report, "hub_api_url")["status"] == "ok"
    assert _check(report, "hub_credential_store")["status"] == "ok"


def test_doctor_rejects_remote_hub_without_explicit_opt_in(monkeypatch, tmp_path):
    _base_environment(monkeypatch, tmp_path)
    monkeypatch.setenv("HUB_INTEGRATION_ENABLED", "true")
    monkeypatch.setenv("HUB_API_BASE_URL", "https://hub.example.test")
    monkeypatch.setenv("HUB_ALLOW_REMOTE", "false")

    report = doctor.collect_report()

    assert report["ready"] is False
    assert _check(report, "hub_api_url")["status"] == "error"


def test_doctor_fails_closed_when_enabled_hub_has_no_trusted_keyring(
    monkeypatch, tmp_path
):
    _base_environment(monkeypatch, tmp_path)
    monkeypatch.setenv("HUB_INTEGRATION_ENABLED", "true")
    monkeypatch.setenv("HUB_API_BASE_URL", "http://127.0.0.1:8000")

    def unavailable():
        raise SecretStoreError("not available")

    monkeypatch.setattr(doctor, "KeyringCredentialStore", unavailable)
    report = doctor.collect_report()

    assert report["ready"] is False
    assert _check(report, "hub_credential_store")["status"] == "error"


def test_doctor_json_output_is_machine_readable_and_redacts_token(
    monkeypatch, tmp_path, capsys
):
    _base_environment(monkeypatch, tmp_path)
    token = "123456:abcdefghijklmnopqrstuvwxyz123456"

    exit_code = doctor.main(json_output=True)
    output = capsys.readouterr().out
    report = json.loads(output)

    assert exit_code == 0
    assert report["ready"] is True
    assert token not in output
    assert report["secrets_displayed"] is False


@pytest.mark.parametrize(
    "token",
    [
        "not-numeric:abcdefghijklmnopqrstuvwxyz",
        "123456:",
        "123456:token with whitespace",
        "１２３４５６:abcdefghijklmnopqrstuvwxyz",
    ],
)
def test_doctor_rejects_malformed_bot_token_without_disclosing_it(
    monkeypatch, tmp_path, token
):
    _base_environment(monkeypatch, tmp_path)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", token)

    report = doctor.collect_report()
    rendered = doctor.render_json(report)

    assert report["ready"] is False
    assert _check(report, "telegram_bot_token")["status"] == "error"
    assert token not in rendered
    assert "TELEGRAM_BOT_TOKEN" in rendered



def test_doctor_accepts_cloudflare_bridge_without_bot_token_and_redacts_bridge_secret(
    monkeypatch, tmp_path
):
    _base_environment(monkeypatch, tmp_path)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("TELEGRAM_TRANSPORT", "cloudflare_bridge")
    monkeypatch.setenv("TELEGRAM_BRIDGE_URL", "https://bridge.example.test")
    secret = "bridge-shared-secret-value-at-least-32"
    monkeypatch.setenv("TELEGRAM_BRIDGE_SHARED_SECRET", secret)
    monkeypatch.setenv("HUB_INTEGRATION_ENABLED", "true")
    monkeypatch.setenv("HUB_API_BASE_URL", "http://127.0.0.1:8000")
    monkeypatch.setattr(doctor, "KeyringCredentialStore", lambda: object())

    report = doctor.collect_report()
    rendered = doctor.render_json(report)

    assert report["ready"] is True
    assert _check(report, "telegram_transport")["status"] == "ok"
    assert _check(report, "telegram_bot_token")["status"] == "disabled"
    assert _check(report, "telegram_bridge_url")["status"] == "ok"
    assert _check(report, "telegram_bridge_secret")["status"] == "ok"
    assert secret not in rendered
    assert report["network_requests"] == 0
    assert report["filesystem_writes"] == 0


def test_doctor_blocks_cloudflare_bridge_with_missing_bridge_settings(
    monkeypatch, tmp_path
):
    _base_environment(monkeypatch, tmp_path)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("TELEGRAM_TRANSPORT", "cloudflare_bridge")
    monkeypatch.delenv("TELEGRAM_BRIDGE_URL", raising=False)
    monkeypatch.delenv("TELEGRAM_BRIDGE_SHARED_SECRET", raising=False)
    monkeypatch.setenv("HUB_INTEGRATION_ENABLED", "false")

    report = doctor.collect_report()

    assert report["ready"] is False
    assert _check(report, "telegram_bot_token")["status"] == "disabled"
    assert _check(report, "telegram_bridge_url")["status"] == "error"
    assert _check(report, "telegram_bridge_secret")["status"] == "error"
    assert _check(report, "cloudflare_bridge_hub_requirement")["status"] == "error"


def test_doctor_rejects_unknown_telegram_transport(monkeypatch, tmp_path):
    _base_environment(monkeypatch, tmp_path)
    monkeypatch.setenv("TELEGRAM_TRANSPORT", "not-a-transport")

    report = doctor.collect_report()

    assert report["ready"] is False
    assert _check(report, "telegram_transport")["status"] == "error"


def test_doctor_accepts_encrypted_file_store_with_valid_host_key(monkeypatch, tmp_path):
    import base64

    _base_environment(monkeypatch, tmp_path)
    monkeypatch.setenv("HUB_INTEGRATION_ENABLED", "true")
    monkeypatch.setenv("HUB_API_BASE_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("EL_AI_CREDENTIAL_STORE", "encrypted_file")
    monkeypatch.setenv(
        "EL_AI_CREDENTIAL_ENCRYPTION_KEY",
        base64.b64encode(b"k" * 32).decode("ascii"),
    )

    report = doctor.collect_report()

    assert report["ready"] is True
    assert _check(report, "hub_credential_store")["status"] == "ok"
    assert not (tmp_path / "state").exists()


def test_doctor_fails_closed_when_encrypted_file_key_is_missing(monkeypatch, tmp_path):
    _base_environment(monkeypatch, tmp_path)
    monkeypatch.setenv("HUB_INTEGRATION_ENABLED", "true")
    monkeypatch.setenv("HUB_API_BASE_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("EL_AI_CREDENTIAL_STORE", "encrypted_file")
    monkeypatch.delenv("EL_AI_CREDENTIAL_ENCRYPTION_KEY", raising=False)

    report = doctor.collect_report()

    assert report["ready"] is False
    assert _check(report, "hub_credential_store")["status"] == "error"
