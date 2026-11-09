import pytest

from el_ai.config import ConfigurationError, Settings, _hub_url, _owner_ids


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


def test_hub_integration_requires_a_base_url(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abcdefghijklmnop")
    monkeypatch.setenv("HUB_INTEGRATION_ENABLED", "true")
    monkeypatch.delenv("HUB_API_BASE_URL", raising=False)
    with pytest.raises(ConfigurationError, match="HUB_API_BASE_URL"):
        Settings.from_env()


def test_non_loopback_hub_urls_require_https():
    with pytest.raises(ConfigurationError, match="HTTPS"):
        _hub_url("http://example.com:8000")
    with pytest.raises(ConfigurationError, match="HUB_ALLOW_REMOTE"):
        _hub_url("https://hub.example.com")
    assert _hub_url("https://hub.example.com", allow_remote=True) == "https://hub.example.com"
    assert _hub_url("http://127.0.0.1:8000") == "http://127.0.0.1:8000"


def test_hub_url_rejects_credentials_and_query_strings():
    with pytest.raises(ConfigurationError):
        _hub_url("https://user:password@hub.example.com")
    with pytest.raises(ConfigurationError):
        _hub_url("https://hub.example.com?token=secret")


def test_settings_repr_redacts_bot_token(monkeypatch):
    token = "123456:abcdefghijklmnopqrstuvwxyz123456"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", token)
    monkeypatch.setenv("TELEGRAM_OWNER_IDS", "123")
    representation = repr(Settings.from_env())
    assert token not in representation
