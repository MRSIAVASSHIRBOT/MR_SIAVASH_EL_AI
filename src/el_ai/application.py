"""Telegram update routing and authorization policy."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from el_ai.telegram import TelegramClient


class TelegramApplication:
    """Handle private chats only; public mode exposes help but no private features."""

    def __init__(
        self,
        client: TelegramClient,
        *,
        owner_ids: Iterable[int],
        public_enabled: bool = False,
    ) -> None:
        self._client = client
        self._owner_ids = frozenset(owner_ids)
        self._public_enabled = public_enabled

    async def handle_update(self, update: dict[str, Any]) -> None:
        message = update.get("message")
        if not isinstance(message, dict):
            return
        chat = message.get("chat")
        sender = message.get("from")
        if not isinstance(chat, dict) or not isinstance(sender, dict):
            return
        if chat.get("type") != "private":
            return

        try:
            chat_id = int(chat.get("id"))
            user_id = int(sender.get("id"))
        except (TypeError, ValueError):
            return
        if chat_id <= 0 or user_id <= 0:
            return

        is_owner = user_id in self._owner_ids
        if not is_owner and not self._public_enabled:
            return

        text = message.get("text")
        if not isinstance(text, str) or not text.strip():
            return
        command = text.strip().split(maxsplit=1)[0].split("@", maxsplit=1)[0].lower()

        if command == "/start":
            response = (
                "MR_SIAVASH_EL_AI آماده است. دسترسی مالک تأیید شد."
                if is_owner
                else "به MR_SIAVASH_EL_AI خوش آمدید. این رابط عمومی فقط راهنمای پایه را ارائه می‌کند."
            )
        elif command == "/help":
            response = (
                "فرمان‌های فعال: /start، /help، /status، /whoami"
                if is_owner
                else "فرمان‌های عمومی: /start، /help، /status"
            )
        elif command == "/status":
            response = (
                "وضعیت: transport=active؛ agent=not_configured؛ memory=Hub-managed/not_connected؛ "
                "planning=not_configured؛ execution=disabled"
                if is_owner
                else "رابط عمومی فعال است؛ قابلیت‌های خصوصی و عامل هوشمند در دسترس عمومی نیستند."
            )
        elif command == "/whoami":
            response = (
                f"شناسهٔ کاربری شما: {user_id}"
                if is_owner
                else "این فرمان فقط برای مالک فعال است."
            )
        else:
            response = (
                "هستهٔ مکالمهٔ هوشمند هنوز به‌صورت کنترل‌شده متصل نشده است. "
                "پیام شما ذخیره یا به سرویس خارجی ارسال نشد."
                if is_owner
                else "گفت‌وگوی عمومی در این نسخه فعال نیست."
            )

        await self._client.send_message(chat_id, response)
