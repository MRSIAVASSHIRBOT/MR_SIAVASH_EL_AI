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
                "\n/tasks [STATUS] [CURSOR] — فهرست صفحه‌بندی‌شده وظایف این ربات"
                "\n/memory QUERY — جست‌وجوی حافظه"
                "\n/memoryentry MEMORY_ID — مشاهده یک یادداشت پیش از تأیید"
                "\n/memoryapprove MEMORY_ID — تأیید صریح همان یادداشت با مجوز جداگانه"
                "\n/memorydream — بازبینی تکراری‌های حافظه بدون تغییر خودکار"
                "\n/memorycheck — بررسی تناقض‌ها و یادداشت‌های تأییدنشده"
                "\n/task TYPE | DESCRIPTION — ثبت وظیفه"
                "\n/taskstatus TASK_ID — مشاهده وضعیت وظیفه ثبت‌شده"
                "\n/taskhistory TASK_ID [CURSOR] — تاریخچه صفحه‌بندی‌شده وظیفه"
                "\n/taskwatch TASK_ID — دریافت اعلان تغییر وضعیت وظیفه"
                "\n/taskunwatch TASK_ID — توقف اعلان‌های وظیفه"
                "\n/taskwatches — فهرست پایش‌های فعال"
                "\n/taskresult TASK_ID — دریافت نتیجه نهایی وظیفه"
                "\n/taskcancel TASK_ID — لغو وظیفه در صف یا منتظر تأیید"
                "\n/videoproviders — وضعیت ارائه‌دهندگان تولید ویدئو"
                "\n/videoroute SECONDS ASPECT [720p|1080p] [remote-ok spend-ok USD_CAP] "
                "— پیش‌نمایش مسیر؛ تولید شروع نمی‌شود"
                "\n/videohistory [CURSOR] — تاریخچه صفحه‌بندی‌شده تولید ویدئو"
                "\n/videostatus GENERATION_ID — وضعیت یک تولید ویدئو"
                "\n/videocancel GENERATION_ID — لغو تولید پیش از ارسال به ارائه‌دهنده"
                "\n/videodrafts ANALYSIS_ID — فهرست Draftهای آمادهٔ رندر"
                "\n/videorenders [CURSOR] — تاریخچه صفحه‌بندی‌شده رندرهای محلی"
                "\n/videorendercheck DRAFT_ID — بررسی آمادگی محلی پیش از ثبت رندر"
                "\n/videorender DRAFT_ID — ثبت رندر قطعی محلی با FFmpeg"
                "\n/videorenderdownload RENDER_ID — ارسال فایل MP4 خصوصی در تلگرام"
                "\n/videorenderstatus RENDER_ID — وضعیت رندر محلی"
                "\n/videorendercancel RENDER_ID — لغو یا توقف رندر محلی"
                "\n/videoscenes ASSET_ID [THRESHOLD] [MAX_CHANGES] — شروع تحلیل برش‌های محلی ویدئو"
                "\n/videoscenestatus JOB_ID — وضعیت و زمان‌های برش تحلیل صحنه"
                "\n/videoscenecancel JOB_ID — لغو تحلیل صحنه"
                "\n/videoscenehistory [CURSOR] — تاریخچه صفحه‌بندی‌شده تحلیل صحنه"
                "\n/videosceneexport JOB_ID — خروجی CSV زمان‌های برش"
                "\n/health — وضعیت و آمار زنده ربات (فقط مالک)"
                "\n/site QUERY — پرسش عمومی از دانش سایت"
            )
            public_help = "\nارسال پرسش متنی — دریافت پاسخ از دانش عمومی سایت"
            return common + (owner_commands if owner else public_help)
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
            return "پیامت دریافت شد. برای پرسش از دانش عمومی سایت از /site استفاده کن."
        return "دستیار عمومی در حال آماده‌سازی است. فعلاً از /help برای دیدن امکانات فعال استفاده کن."
