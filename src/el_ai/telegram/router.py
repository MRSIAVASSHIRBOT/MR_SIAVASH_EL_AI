"""Minimal command router. Private Hub operations are intentionally unavailable."""

from __future__ import annotations

from el_ai.telegram.access import AccessController


class MessageRouter:
    def __init__(self, access: AccessController, *, client_id: str | None = None) -> None:
        self._access = access
        self._client_id = client_id

    def route(self, message: dict) -> str | None:
        sender = message.get("from") or {}
        chat = message.get("chat") or {}
        user_id = sender.get("id") if isinstance(sender.get("id"), int) else None
        chat_type = chat.get("type") if isinstance(chat.get("type"), str) else None
        if not self._access.allow_message(user_id=user_id, chat_type=chat_type):
            return None
        text = message.get("text")
        if not isinstance(text, str):
            return "در نسخهٔ فعلی فقط پیام متنی پشتیبانی می‌شود."
        normalized = text.strip()
        if not normalized:
            return "پیام خالی است؛ لطفاً متن درخواست را ارسال کن."
        command = normalized.split(maxsplit=1)[0].split("@", 1)[0].lower()
        owner = self._access.is_owner(user_id)
        if command == "/start":
            return (
                "سلام! ✨ من MR_SIAVASH_EL_AI هستم. برای راهنما /help را بفرست."
                if owner
                else "سلام! به MR_SIAVASH_EL_AI خوش آمدی. برای راهنما /help را بفرست."
            )
        if command == "/help":
            common = "/start — شروع\n/help — راهنما"
            owner_commands = (
                "\n/owner — وضعیت دسترسی مالک"
                "\n/pairinfo — شناسه اتصال Hub"
                "\n/pair CODE — اتصال امن به Hub"
                "\n/hub — وضعیت اتصال"
                "\n/tasks — فهرست وظایف"
                "\n/memory QUERY — جست‌وجوی حافظه"
                "\n/task TYPE | DESCRIPTION — ثبت وظیفه"
            )
            return common + (owner_commands if owner else "")
        if command == "/pairinfo":
            if not owner:
                return None
            return (
                f"Telegram ID: {user_id}\n"
                f"EL_AI Client ID: {self._client_id or 'not initialized'}\n"
                "این شناسه‌ها را فقط در پنل محلی Hub برای ساخت Pairing Challenge استفاده کن."
            )
        if command == "/owner":
            return (
                "دسترسی مالک تأیید شد. قابلیت‌های خصوصی فقط از طریق مجوزهای فعال Hub قابل استفاده‌اند."
                if owner
                else None
            )
        if command.startswith("/"):
            return "این دستور در نسخهٔ فعلی فعال نیست. /help را ببین."
        if owner:
            return "پیامت دریافت شد. موتور مکالمه و اتصال امن به Hub هنوز در حال پیاده‌سازی است."
        return "دستیار عمومی در حال آماده‌سازی است. فعلاً از /help برای دیدن امکانات فعال استفاده کن."
