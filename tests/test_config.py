import pytest

from el_ai.config import ConfigurationError, Settings, _owner_ids


def test_settings_require_bot_token(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    with pytest.raises(ConfigurationError, match="TELEGRAM_BOT_TOKEN"):
        Settings.from_env()


def test_owner_ids_are_positive_numeric_and_deduplicated():
    assert _owner_ids("123, 456,123") == frozenset({123, 456})
    with pytest.raises(ConfigurationError):
        _owner_ids("123,not-a-number")
    with pytest.raises(ConfigurationError):
        _owner_ids("0")


def test_hub_integration_cannot_be_enabled_before_pairing(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("HUB_INTEGRATION_ENABLED", "true")
    with pytest.raises(ConfigurationError, match="owner pairing"):
        Settings.from_env()
