"""Long-polling runtime for local development and controlled first release."""

from __future__ import annotations

import asyncio
import json
import logging
import signal

from el_ai.config import ConfigurationError, Settings
from el_ai.integrations.hub import HubClient, HubClientError
from el_ai.integrations.identity import InstanceIdentityError, load_or_create_client_id
from el_ai.integrations.secrets import KeyringCredentialStore, SecretStoreError
from el_ai.integrations.site_gateway import SiteGatewayClient, SiteGatewayError
from el_ai.telegram.access import AccessController
from el_ai.telegram.api import TelegramAPIError, TelegramBotAPI, split_telegram_message
from el_ai.telegram.router import MessageRouter

LOGGER = logging.getLogger("el_ai")


def _next_update_offset(offset: int | None, update_id: object) -> int | None:
    """Return the next Telegram offset after an update has been fully handled."""
    if isinstance(update_id, int):
        return max(offset or 0, update_id + 1)
    return offset


def _format_result(value: dict[str, object]) -> str:
    """Serialize owner-scoped Hub results without dropping response content."""
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


async def _send_response(api: TelegramBotAPI, chat_id: int, response: str) -> None:
    """Deliver the complete response in chunks accepted by the Telegram Bot API."""
    for chunk in split_telegram_message(response):
        await api.send_message(chat_id, chunk)


def _build_hub_client(settings: Settings, client_id: str) -> HubClient | None:
    """Initialize Hub access only after the explicit integration opt-in."""
    if not settings.hub_integration_enabled or not settings.hub_api_base_url:
        return None
    try:
        store = KeyringCredentialStore()
        return HubClient(
            settings.hub_api_base_url,
            client_id,
            store,
            allow_remote=settings.hub_allow_remote,
        )
    except SecretStoreError:
        LOGGER.warning("OS credential store unavailable; Hub integration is disabled")
        return None


async def _owner_hub_command(
    text: str,
    *,
    telegram_user_id: int,
    update_id: int,
    hub: HubClient | None,
    settings: Settings,
) -> str | None:
    parts = text.strip().split(maxsplit=1)
    command = parts[0].split("@", 1)[0].lower()
    argument = parts[1].strip() if len(parts) > 1 else ""

    if command == "/pair":
        if not settings.hub_integration_enabled:
            return "اتصال Hub غیرفعال است؛ برای فعال‌سازی، HUB_INTEGRATION_ENABLED=true را آگاهانه تنظیم کن."
        if hub is None:
            return "اتصال Hub آماده نیست؛ آدرس محلی Hub یا ذخیره‌ساز امن سیستم در دسترس نیست."
        if not argument:
            return "روش اتصال: /pair CODE — کد یک‌بارمصرف را از Hub محلی دریافت کن."
        try:
            result = await hub.pair(argument, telegram_user_id)
            return (
                "اتصال امن با Hub برقرار شد.\n"
                f"Client ID: {result['client_id']}\n"
                f"Scopes: {', '.join(result.get('scopes', []))}"
            )
        except HubClientError:
            return "اتصال انجام نشد؛ کد ممکن است نامعتبر یا منقضی شده باشد. هیچ اطلاعات محرمانه‌ای ثبت نشده است."

    if command not in {"/hub", "/tasks", "/memory", "/task"}:
        return None
    if not settings.hub_integration_enabled:
        return "اتصال Hub هنوز در تنظیمات این اجرا فعال نشده است."
    if hub is None:
        return "ذخیره‌ساز امن سیستم یا آدرس Hub در دسترس نیست."
    try:
        if command == "/hub":
            return _format_result(await hub.status(telegram_user_id))
        if command == "/tasks":
            return _format_result(await hub.list_tasks(telegram_user_id))
        if command == "/memory":
            if not argument:
                return "روش استفاده: /memory عبارت جست‌وجو"
            return _format_result(await hub.search_memory(telegram_user_id, argument[:200]))
        if command == "/task":
            if "|" not in argument:
                return "روش استفاده: /task task.type | توضیح وظیفه"
            task_type, description = (part.strip() for part in argument.split("|", 1))
            if not task_type:
                return "نوع وظیفه خالی است."
            return _format_result(
                await hub.create_task(
                    telegram_user_id,
                    task_type[:80],
                    description[:500],
                    idempotency_key=f"telegram-update-{update_id}",
                )
            )
    except HubClientError:
        return "درخواست Hub ناموفق بود یا مجوز لازم وجود ندارد. وضعیت اتصال و Scopeها را بررسی کن."
    return None


async def run(settings: Settings) -> None:
    try:
        client_id = load_or_create_client_id(settings.state_dir)
    except InstanceIdentityError as exc:
        raise SystemExit(str(exc)) from exc

    api = TelegramBotAPI(
        settings.telegram_bot_token,
        timeout_seconds=settings.request_timeout_seconds,
    )
    access = AccessController(
        settings.owner_ids,
        public_enabled=settings.public_enabled,
        rate_limit=settings.rate_limit_per_minute,
    )
    router = MessageRouter(access, client_id=client_id)
    hub = _build_hub_client(settings, client_id)
    site = SiteGatewayClient(
        settings.site_public_api_base_url,
        settings.site_public_web_base_url,
        timeout_seconds=min(settings.request_timeout_seconds, 12),
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    offset: int | None = None
    backoff = 1

    try:
        LOGGER.info("Telegram polling started; environment=%s", settings.environment)
        while not stop.is_set():
            try:
                updates = await api.get_updates(offset=offset, timeout=settings.poll_timeout_seconds)
                backoff = 1
                for update in updates:
                    update_id = update.get("update_id")
                    message = update.get("message")
                    if not isinstance(message, dict):
                        offset = _next_update_offset(offset, update_id)
                        continue
                    chat = message.get("chat") or {}
                    chat_id = chat.get("id")
                    if not isinstance(chat_id, int):
                        acknowledge_update(update_id)
                        continue
                    response = router.route(message)
                    if response is None:
                        acknowledge_update(update_id)
                        continue

                    sender = message.get("from") or {}
                    user_id = sender.get("id") if isinstance(sender.get("id"), int) else None
                    text = message.get("text", "")
                    normalized_text = text.strip() if isinstance(text, str) else ""
                    command_name = (
                        normalized_text.split(maxsplit=1)[0].split("@", 1)[0].lower()
                        if normalized_text else ""
                    )
                    if user_id is not None and access.is_owner(user_id):
                        if command_name in {"/pair", "/hub", "/tasks", "/memory", "/task"}:
                            response = await _owner_hub_command(
                                text,
                                telegram_user_id=user_id,
                                update_id=update_id if isinstance(update_id, int) else 0,
                                hub=hub,
                                settings=settings,
                            ) or response
                        elif command_name == "/site":
                            query = normalized_text.partition(" ")[2].strip()
                            if not query:
                                response = "روش استفاده: /site پرسش عمومی درباره سایت"
                            else:
                                try:
                                    result = await site.ask(query)
                                    response = site.format_answer(result)
                                except ValueError:
                                    response = "پرسش عمومی باید بین ۱ تا ۱۲۰۰ نویسه باشد."
                                except SiteGatewayError:
                                    response = "درگاه عمومی سایت فعلاً در دسترس نیست؛ کمی بعد دوباره تلاش کن."
                    elif (
                        user_id is not None
                        and not access.is_owner(user_id)
                        and settings.public_enabled
                        and normalized_text
                        and not normalized_text.startswith("/")
                    ):
                        try:
                            result = await site.ask(normalized_text)
                            response = site.format_answer(result)
                        except ValueError:
                            response = "پرسش عمومی باید بین ۱ تا ۱۲۰۰ نویسه باشد."
                        except SiteGatewayError:
                            response = "دستیار عمومی سایت فعلاً در دسترس نیست؛ کمی بعد دوباره تلاش کن."
                    try:
                        await _send_response(api, chat_id, response)
                    except TelegramAPIError:
                        LOGGER.warning(
                            "Telegram reply failed or has unknown outcome; update=%s",
                            update_id,
                        )
                        raise
                    else:
                        acknowledge_update(update_id)
            except TelegramAPIError:
                LOGGER.warning("Telegram polling request failed; retrying with bounded backoff")
                try:
                    await asyncio.wait_for(stop.wait(), timeout=backoff)
                except TimeoutError:
                    pass
                backoff = min(backoff * 2, 30)
    finally:
        if hub is not None:
            await hub.close()
        await site.close()
        await api.close()
        LOGGER.info("Telegram polling stopped")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        settings = Settings.from_env()
    except ConfigurationError as exc:
        raise SystemExit(f"Configuration error: {exc}") from exc
    asyncio.run(run(settings))


if __name__ == "__main__":
    main()
