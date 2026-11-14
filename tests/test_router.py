from el_ai.telegram.access import AccessController
from el_ai.telegram.router import MessageRouter


def _message(text: str, user_id: int = 123, chat_type: str = "private") -> dict:
    return {"text": text, "from": {"id": user_id}, "chat": {"id": 900, "type": chat_type}}


def test_owner_commands_are_separated_from_public_commands():
    router = MessageRouter(AccessController(frozenset({123}), public_enabled=True, rate_limit=20))
    assert "دسترسی مالک" in router.route(_message("/owner"))
    assert router.route(_message("/owner", user_id=456)) is None
    assert "/owner" not in router.route(_message("/help", user_id=456))


def test_group_messages_are_rejected():
    router = MessageRouter(AccessController(frozenset({123}), public_enabled=True, rate_limit=20))
    assert router.route(_message("/start", chat_type="group")) is None


def test_hub_execution_is_not_claimed_as_available():
    router = MessageRouter(AccessController(frozenset({123}), public_enabled=False, rate_limit=20))
    assert "برای پرسش از دانش عمومی سایت" in router.route(_message("سلام"))


def test_empty_text_is_handled_without_crashing():
    router = MessageRouter(AccessController(frozenset({123}), public_enabled=False, rate_limit=20))
    assert "خالی" in router.route(_message("   "))


def test_pairing_identity_is_owner_only():
    router = MessageRouter(
        AccessController(frozenset({123}), public_enabled=True, rate_limit=20),
        client_id="00000000-0000-4000-8000-000000000001",
    )
    owner_info = router.route(_message("/pairinfo"))
    public_info = router.route(_message("/pairinfo", user_id=456))
    assert "Telegram ID: 123" in owner_info
    assert "00000000-0000-4000-8000-000000000001" in owner_info
    assert public_info is None



def test_help_documents_public_site_query_for_owner_and_public_user():
    router = MessageRouter(
        AccessController(frozenset({123}), public_enabled=True, rate_limit=20)
    )
    assert "/site QUERY" in router.route(_message("/help"))
    assert "/taskstatus TASK_ID" in router.route(_message("/help"))
    assert "/tasks [CURSOR]" in router.route(_message("/help"))
    assert "/health" in router.route(_message("/help"))
    public_help = router.route(_message("/help", user_id=456))
    assert "ارسال پرسش متنی" in public_help
    assert "/taskstatus" not in public_help
    assert "/tasks" not in public_help
    assert "/health" not in public_help
