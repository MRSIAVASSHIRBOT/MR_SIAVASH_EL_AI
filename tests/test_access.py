from el_ai.telegram.access import AccessController


def test_owner_is_allowed_only_in_private_chat():
    access = AccessController(frozenset({123}), public_enabled=False, rate_limit=5)
    assert access.allow_message(user_id=123, chat_type="private")
    assert not access.allow_message(user_id=123, chat_type="group")
    assert not access.allow_message(user_id=456, chat_type="private")


def test_public_access_is_opt_in_and_private_only():
    disabled = AccessController(frozenset(), public_enabled=False, rate_limit=5)
    enabled = AccessController(frozenset(), public_enabled=True, rate_limit=5)
    assert not disabled.allow_message(user_id=456, chat_type="private")
    assert enabled.allow_message(user_id=456, chat_type="private")
    assert not enabled.allow_message(user_id=456, chat_type="supergroup")


def test_rate_limit_is_enforced():
    access = AccessController(frozenset({123}), public_enabled=False, rate_limit=2)
    assert access.allow_message(user_id=123, chat_type="private")
    assert access.allow_message(user_id=123, chat_type="private")
    assert not access.allow_message(user_id=123, chat_type="private")
