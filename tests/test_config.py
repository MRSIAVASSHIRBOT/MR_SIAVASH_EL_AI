import pytest

from el_ai.config import ConfigurationError, Settings, _hub_url, _owner_ids, _public_url


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



def test_public_site_urls_default_to_the_verified_site_gateway(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abcdefghijklmnop")
    monkeypatch.setenv("TELEGRAM_OWNER_IDS", "123")
    settings = Settings.from_env()
    assert settings.site_public_api_base_url == "https://mr-siavash-ai-gateway.mrsiavashirbot.workers.dev"
    assert settings.site_public_web_base_url == "https://mrsiavasshirbot.github.io/MR_SIAVASH_AI"


def test_public_site_url_rejects_remote_plain_http_and_credentials():
    with pytest.raises(ConfigurationError, match="HTTPS"):
        _public_url("http://gateway.example", "https://gateway.example", "SITE_PUBLIC_API_BASE_URL")
    with pytest.raises(ConfigurationError):
        _public_url("https://user:password@gateway.example", "https://gateway.example", "SITE_PUBLIC_API_BASE_URL")


@pytest.mark.parametrize(
    "token",
    [
        "not-numeric:abcdefghijklmnopqrstuvwxyz",
        "123456:",
        "123456:token with whitespace",
        "１２３４５６:abcdefghijklmnopqrstuvwxyz",
    ],
)
def test_settings_reject_malformed_telegram_bot_token(monkeypatch, token):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", token)
    with pytest.raises(ConfigurationError, match="TELEGRAM_BOT_TOKEN"):
        Settings.from_env()



def test_cloudflare_bridge_mode_does_not_require_telegram_bot_token(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("TELEGRAM_TRANSPORT", "cloudflare_bridge")
    monkeypatch.setenv("TELEGRAM_BRIDGE_URL", "https://bridge.example.test")
    monkeypatch.setenv("TELEGRAM_BRIDGE_SHARED_SECRET", "x" * 40)
    monkeypatch.setenv("TELEGRAM_OWNER_IDS", "123")
    monkeypatch.setenv("HUB_INTEGRATION_ENABLED", "true")
    monkeypatch.setenv("HUB_API_BASE_URL", "https://hub.example.test")
    monkeypatch.setenv("HUB_ALLOW_REMOTE", "true")

    settings = Settings.from_env()

    assert settings.telegram_bot_token is None
    assert settings.telegram_transport == "cloudflare_bridge"
    assert settings.telegram_bridge_url == "https://bridge.example.test"
    assert settings.telegram_bridge_shared_secret == "x" * 40


@pytest.mark.parametrize(
    "name,value",
    [
        ("TELEGRAM_TRANSPORT", "unknown"),
        ("TELEGRAM_BRIDGE_URL", "http://bridge.example.test"),
        ("TELEGRAM_BRIDGE_SHARED_SECRET", "too-short"),
    ],
)
def test_cloudflare_bridge_mode_rejects_invalid_configuration(monkeypatch, name, value):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("TELEGRAM_TRANSPORT", "cloudflare_bridge")
    monkeypatch.setenv("TELEGRAM_BRIDGE_URL", "https://bridge.example.test")
    monkeypatch.setenv("TELEGRAM_BRIDGE_SHARED_SECRET", "x" * 40)
    monkeypatch.setenv("TELEGRAM_OWNER_IDS", "123")
    monkeypatch.setenv("HUB_INTEGRATION_ENABLED", "true")
    monkeypatch.setenv("HUB_API_BASE_URL", "https://hub.example.test")
    monkeypatch.setenv("HUB_ALLOW_REMOTE", "true")
    monkeypatch.setenv(name, value)

    with pytest.raises(ConfigurationError):
        Settings.from_env()


def test_cloudflare_bridge_mode_requires_owner_allowlist_and_hub(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("TELEGRAM_TRANSPORT", "cloudflare_bridge")
    monkeypatch.setenv("TELEGRAM_BRIDGE_URL", "https://bridge.example.test")
    monkeypatch.setenv("TELEGRAM_BRIDGE_SHARED_SECRET", "x" * 40)
    monkeypatch.delenv("TELEGRAM_OWNER_IDS", raising=False)
    monkeypatch.delenv("HUB_INTEGRATION_ENABLED", raising=False)
    monkeypatch.delenv("HUB_API_BASE_URL", raising=False)

    with pytest.raises(ConfigurationError, match="TELEGRAM_OWNER_IDS"):
        Settings.from_env()



def test_local_polling_ignores_bridge_only_configuration(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abcdefghijklmnop")
    monkeypatch.setenv("TELEGRAM_TRANSPORT", "local_polling")
    monkeypatch.setenv("TELEGRAM_BRIDGE_TIMEOUT_SECONDS", "not-an-integer")
    settings = Settings.from_env()
    assert settings.telegram_transport == "local_polling"
    assert settings.telegram_bridge_timeout_seconds == 8



def test_render_private_hub_hostport_requires_explicit_render_opt_in():
    assert (
        _hub_url("mr-siavash-hub-ai:10000", allow_render_private_network=True)
        == "http://mr-siavash-hub-ai:10000"
    )
    with pytest.raises(ConfigurationError):
        _hub_url("mr-siavash-hub-ai:10000")
    with pytest.raises(ConfigurationError):
        _hub_url("hub.example.com:10000", allow_render_private_network=True)
    with pytest.raises(ConfigurationError):
        _hub_url("127.0.0.1:10000", allow_render_private_network=True)
    with pytest.raises(ConfigurationError):
        _hub_url("mr-siavash-hub-ai", allow_render_private_network=True)
    with pytest.raises(ConfigurationError):
        _hub_url("mr-siavash-hub-ai:0", allow_render_private_network=True)
    for invalid in (
        "123:10000",
        "mr-siavash-hub-ai:0",
        "mr-siavash-hub-ai:65536",
        "mr-siavash-hub-ai:not-a-port",
        "mr-siavash-hub-ai:10000/path",
    ):
        with pytest.raises(ConfigurationError):
            _hub_url(invalid, allow_render_private_network=True)


def test_cloudflare_bridge_accepts_explicit_render_private_hub_binding(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("TELEGRAM_TRANSPORT", "cloudflare_bridge")
    monkeypatch.setenv("TELEGRAM_BRIDGE_URL", "https://bridge.example.test")
    monkeypatch.setenv("TELEGRAM_BRIDGE_SHARED_SECRET", "x" * 40)
    monkeypatch.setenv("TELEGRAM_OWNER_IDS", "123")
    monkeypatch.setenv("HUB_INTEGRATION_ENABLED", "true")
    monkeypatch.setenv("HUB_API_BASE_URL", "mr-siavash-hub-ai:10000")
    monkeypatch.setenv("HUB_ALLOW_REMOTE", "false")
    monkeypatch.setenv("HUB_ALLOW_RENDER_PRIVATE_NETWORK", "true")

    settings = Settings.from_env()

    assert settings.hub_api_base_url == "http://mr-siavash-hub-ai:10000"
    assert settings.hub_allow_render_private_network is True
