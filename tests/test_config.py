import pytest

from el_ai.config import ConfigurationError, Settings

TOKEN = "123456:abcdefghijklmnopqrstuvwxyz123456"
BASE_ENV = {
    "TELEGRAM_BOT_TOKEN": TOKEN,
    "TELEGRAM_OWNER_IDS": "123456789",
}


def test_settings_default_to_owner_only_and_valid_timeouts():
    settings = Settings.from_env(BASE_ENV)

    assert settings.owner_ids == frozenset({123456789})
    assert settings.public_enabled is False
    assert settings.poll_timeout_seconds == 25
    assert settings.request_timeout_seconds == 35.0


def test_owner_ids_are_parsed_and_deduplicated():
    settings = Settings.from_env({**BASE_ENV, "TELEGRAM_OWNER_IDS": "12, 34,12"})

    assert settings.owner_ids == frozenset({12, 34})


@pytest.mark.parametrize(
    "value",
    ["", "abc", "12,invalid", "0", "-1"],
)
def test_invalid_owner_ids_fail_closed(value):
    with pytest.raises(ConfigurationError):
        Settings.from_env({**BASE_ENV, "TELEGRAM_OWNER_IDS": value})


def test_token_is_not_in_settings_repr():
    settings = Settings.from_env(BASE_ENV)

    assert TOKEN not in repr(settings)


def test_public_mode_requires_explicit_true_value():
    settings = Settings.from_env({**BASE_ENV, "TELEGRAM_PUBLIC_ENABLED": "true"})
    assert settings.public_enabled is True

    with pytest.raises(ConfigurationError):
        Settings.from_env({**BASE_ENV, "TELEGRAM_PUBLIC_ENABLED": "sometimes"})


def test_polling_timeout_must_be_shorter_than_http_timeout():
    with pytest.raises(ConfigurationError):
        Settings.from_env({
            **BASE_ENV,
            "TELEGRAM_POLL_TIMEOUT_SECONDS": "40",
            "TELEGRAM_REQUEST_TIMEOUT_SECONDS": "35",
        })
