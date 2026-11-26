"""Long-polling runtime for local development and controlled first release."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import re
import signal
import time
from uuid import UUID

from dotenv import load_dotenv

from el_ai.config import ConfigurationError, Settings
from el_ai.integrations.hub import HubClient, HubClientError
from el_ai.integrations.identity import InstanceIdentityError, load_or_create_client_id
from el_ai.integrations.secrets import KeyringCredentialStore, build_credential_store
from el_ai.integrations.site_gateway import SiteGatewayClient, SiteGatewayError
from el_ai.observability import RuntimeMetrics
from el_ai.telegram.access import AccessController
from el_ai.telegram.business_connections import BusinessConnectionError, BusinessConnectionStore
from el_ai.telegram.business_processor import BusinessEventProcessor
from el_ai.telegram.api import TelegramAPIError, TelegramBotAPI, split_telegram_message
from el_ai.telegram.bridge import BridgeJob, TelegramBridgeClient
from el_ai.telegram.bridge_worker import TelegramBridgeWorker
from el_ai.telegram.cursor import UpdateCursor, UpdateCursorError
from el_ai.telegram.router import MessageRouter
from el_ai.telegram.task_plan import TaskPlanError, TaskPlanStore
from el_ai.telegram.task_plan_orchestrator import advance_ready_plan_steps
from el_ai.telegram.task_plan_evaluation import evaluate_task_plan_evidence
from el_ai.telegram.task_watch import (
    TERMINAL_TASK_STATUSES,
    TaskWatch,
    TaskWatchError,
    TaskWatchStore,
)
from el_ai.telegram.updates import ParsedTelegramUpdate, TelegramUpdateError, parse_telegram_update

LOGGER = logging.getLogger("el_ai")

TASK_STATUSES = frozenset({
    "queued", "running", "waiting_approval", "succeeded",
    "failed", "cancelled", "rejected",
})
OWNER_HUB_COMMANDS = frozenset({
    "/pair", "/doctor", "/hub", "/tasks", "/memory", "/memoryentry", "/memoryapprove", "/memorydream", "/memorycheck", "/task", "/taskstatus", "/taskprogress", "/taskplan", "/agentplan", "/taskplanrun", "/taskplans", "/taskplanstatus", "/taskplanwatch", "/taskplancancel", "/taskplanpause", "/taskplanresume", "/taskplanspauseall", "/taskplansresumeall", "/taskplanevaluate",
    "/taskresult", "/taskcancel", "/taskhistory", "/taskwatch", "/taskunwatch",
    "/taskwatches", "/videoproviders", "/videoroute",
    "/videohistory", "/videostatus", "/videocancel",
    "/videodrafts", "/videorenders", "/videorendercheck", "/videorender", "/videorenderstatus",
    "/videorenderdownload", "/videorendercancel",
    "/videoscenes", "/videoscenestatus", "/videoscenecancel",
    "/videoscenehistory", "/videosceneexport",
})

# This is intentionally narrower than OWNER_HUB_COMMANDS and must match the edge Worker.
BRIDGE_OWNER_COMMANDS = frozenset({
    "/doctor", "/hub", "/tasks", "/memory", "/memoryentry", "/memoryapprove",
    "/memorydream", "/memorycheck", "/task", "/taskstatus", "/taskprogress",
    "/taskplan", "/agentplan", "/taskplanrun", "/taskplans", "/taskplanstatus",
    "/taskplancancel", "/taskplanpause", "/taskplanresume", "/taskplanspauseall", "/taskplansresumeall", "/taskplanevaluate", "/taskresult",
    "/taskcancel", "/taskhistory", "/videoproviders", "/videoroute",
    "/videohistory", "/videostatus", "/videocancel", "/videodrafts",
    "/videorenders", "/videorendercheck", "/videorender",
    "/videorenderstatus", "/videorendercancel", "/videoscenes",
    "/videoscenestatus", "/videoscenecancel", "/videoscenehistory",
    "/videosceneexport",
})


def _next_update_offset(offset: int | None, update_id: object) -> int | None:
    """Return the next Telegram offset after an update has been fully handled."""
    if isinstance(update_id, int) and not isinstance(update_id, bool):
        return max(offset or 0, update_id + 1)
    return offset


def _advance_update_cursor(
    cursor: UpdateCursor,
    offset: int | None,
    update_id: object,
) -> int | None:
    """Persist an acknowledged update before using its offset on the next poll."""
    next_offset = _next_update_offset(offset, update_id)
    if next_offset is not None and next_offset != offset:
        cursor.save(next_offset)
    return next_offset


def _process_business_update(
    update: ParsedTelegramUpdate,
    processor: BusinessEventProcessor,
):
    """Dispatch only the separately normalized Business event, never a regular message."""
    if update.business_event is None:
        return None
    return processor.process(update)


def _format_result(value: dict[str, object]) -> str:
    """Serialize owner-scoped Hub results without dropping response content."""
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _parse_task_plan_spec(raw_steps: str) -> tuple[list[str], list[list[int]] | None]:
    """Parse sequential shorthand or an explicit 1-based dependency DAG.

    Explicit syntax: 1: Collect ; 2: Validate <- 1 ; 3: Publish <- 1,2.
    Dependencies must refer to earlier numbered steps; the store validates again.
    """
    parts = [part.strip() for part in raw_steps.split(";")]
    if any(not part for part in parts):
        raise ValueError("Task-plan steps cannot be empty")
    explicit = any("<-" in part or re.match(r"^\d+\s*:", part) for part in parts)
    if not explicit:
        return parts, None

    descriptions: list[str] = []
    dependencies: list[list[int]] = []
    for index, part in enumerate(parts):
        match = re.fullmatch(
            r"(\d+)\s*:\s*(.+?)(?:\s*<-\s*([\d,\s]+))?",
            part,
        )
        if match is None or int(match.group(1)) != index + 1:
            raise ValueError("Explicit task-plan steps must be numbered in order")
        description = match.group(2).strip()
        if not description:
            raise ValueError("Task-plan step description is empty")
        raw_dependencies = match.group(3)
        dependency_indexes: list[int] = []
        if raw_dependencies:
            for raw_dependency in raw_dependencies.split(","):
                number = int(raw_dependency.strip())
                if number < 1 or number > index:
                    raise ValueError("Dependencies must refer to earlier numbered steps")
                dependency_indexes.append(number - 1)
        if len(set(dependency_indexes)) != len(dependency_indexes):
            raise ValueError("A dependency cannot be repeated")
        descriptions.append(description)
        dependencies.append(dependency_indexes)
    return descriptions, dependencies


def _runtime_health_response(metrics: RuntimeMetrics) -> str:
    """Render aggregate polling health without exposing message or identity data."""
    return _format_result(metrics.snapshot())


async def _send_response(api: TelegramBotAPI, chat_id: int, response: str) -> None:
    """Deliver the complete response in chunks accepted by the Telegram Bot API."""
    for chunk in split_telegram_message(response):
        await api.send_message(chat_id, chunk)


def _build_hub_client(settings: Settings, client_id: str) -> HubClient | None:
    """Initialize Hub access only after the explicit integration opt-in."""
    if not settings.hub_integration_enabled or not settings.hub_api_base_url:
        return None
    store_mode = os.getenv("EL_AI_CREDENTIAL_STORE", "keyring").strip().lower()
    store = (
        KeyringCredentialStore()
        if store_mode == "keyring"
        else build_credential_store(settings.state_dir)
    )
    return HubClient(
        settings.hub_api_base_url,
        client_id,
        store,
        allow_remote=settings.hub_allow_remote,
        allow_render_private_network=settings.hub_allow_render_private_network,
    )


def _task_status_label(status: str) -> str:
    return {
        "queued": "در صف",
        "running": "در حال اجرا",
        "waiting_approval": "در انتظار تأیید",
        "succeeded": "موفق",
        "failed": "ناموفق",
        "cancelled": "لغوشده",
        "rejected": "ردشده",
    }.get(status, "نامشخص")


_TASK_EVENT_LABELS = {
    "task.submitted": "وظیفه ثبت شد",
    "task.approved": "وظیفه تأیید شد",
    "task.rejected": "وظیفه رد شد",
    "task.cancelled": "وظیفه لغو شد",
    "task.succeeded": "وظیفه تکمیل شد",
    "task.failed": "اجرای وظیفه ناموفق بود",
    "worker.task_started": "اجرای وظیفه آغاز شد",
    "worker.task_succeeded": "اجرای وظیفه تکمیل شد",
    "worker.task_failed": "اجرای وظیفه ناموفق بود",
    "worker.task_retry_scheduled": "تلاش مجدد زمان‌بندی شد",
    "worker.task_recovered": "وظیفه پس از وقفه بازیابی شد",
}


def _safe_progress_value(value: object, *, limit: int = 80) -> str:
    """Keep untrusted task/event labels printable and bounded for Telegram."""
    if not isinstance(value, str):
        return ""
    return "".join(
        character for character in value
        if character.isalnum() or character in "._-"
    )[:limit]


def _safe_progress_timestamp(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return "".join(
        character for character in value
        if character.isdigit() or character in "TtZz:+-._"
    )[:40]


def _format_task_progress(
    task: dict[str, object],
    history: dict[str, object] | None,
    task_id: str,
) -> str:
    """Render a compact, owner-scoped task timeline without audit metadata."""
    status = task.get("status")
    if not isinstance(status, str) or status not in TASK_STATUSES:
        status = "unknown"
    lines = [
        "گزارش پیشرفت وظیفه",
        f"شناسه: {task_id}",
        f"وضعیت: {_task_status_label(status)}",
    ]
    task_type = _safe_progress_value(task.get("task_type"))
    if task_type:
        lines.append(f"نوع: {task_type}")
    created_at = _safe_progress_timestamp(task.get("created_at"))
    updated_at = _safe_progress_timestamp(task.get("updated_at"))
    if created_at:
        lines.append(f"ثبت: {created_at}")
    if updated_at:
        lines.append(f"آخرین به‌روزرسانی: {updated_at}")

    events = history.get("events") if isinstance(history, dict) else None
    visible_events = [event for event in events if isinstance(event, dict)][:5] if isinstance(events, list) else []
    if visible_events:
        lines.append("رویدادهای اخیر:")
        for event in visible_events:
            event_type = _safe_progress_value(event.get("event_type"))
            label = _TASK_EVENT_LABELS.get(
                event_type,
                event_type.replace(".", " ").replace("_", " ") or "رویداد",
            )
            outcome = event.get("outcome")
            outcome_label = (
                _task_status_label(outcome)
                if isinstance(outcome, str) and outcome in TASK_STATUSES
                else _safe_progress_value(outcome, limit=32)
            )
            timestamp = _safe_progress_timestamp(event.get("created_at"))
            detail = " · ".join(part for part in (timestamp, label, outcome_label) if part)
            lines.append(f"• {detail}")
    else:
        lines.append("هنوز رویداد قابل‌نمایشی برای این وظیفه ثبت نشده است.")

    next_cursor = history.get("next_cursor") if isinstance(history, dict) else None
    if (
        isinstance(next_cursor, str)
        and 1 <= len(next_cursor) <= 512
        and next_cursor.isascii()
        and all(character.isalnum() or character in "-_" for character in next_cursor)
        and history.get("has_more") is True
    ):
        lines.append(f"ادامه تاریخچه: /taskhistory {task_id} {next_cursor}")
    elif history is None:
        lines.append("تاریخچه رویدادها فعلاً در دسترس نیست.")
    return "\n".join(lines)


def _defer_task_watch(
    store: TaskWatchStore,
    watch: TaskWatch,
    now: int,
) -> None:
    """Persist a bounded retry deadline without logging task or owner identifiers."""
    try:
        store.defer(watch.task_id, watch.telegram_user_id, now)
    except TaskWatchError:
        LOGGER.warning("Telegram task-watch retry deadline could not be persisted")


async def _poll_task_watches(
    api: TelegramBotAPI,
    hub: HubClient,
    store: TaskWatchStore,
    *,
    now: int | None = None,
) -> None:
    """Poll due watches and retry transient failures with persisted backoff."""
    current_time = int(time.time()) if now is None else now
    watches = store.list_due(current_time)
    if not watches:
        return
    semaphore = asyncio.Semaphore(4)

    async def read_task(
        watch: TaskWatch,
    ) -> tuple[TaskWatch, dict[str, object] | None, int | None]:
        async with semaphore:
            try:
                task = await hub.get_task(watch.telegram_user_id, watch.task_id)
            except HubClientError as exc:
                return watch, None, exc.status_code if exc.status_code is not None else 0
            except Exception:  # noqa: BLE001 - one unavailable watch must not stop polling
                return watch, None, 0
        status = task.get("status") if isinstance(task, dict) else None
        if not isinstance(task, dict) or not isinstance(status, str) or status not in TASK_STATUSES:
            return watch, None, 0
        return watch, task, None

    results = await asyncio.gather(*(read_task(item) for item in watches))
    for watch, task, error_code in results:
        if error_code == 404:
            message = (
                "پایش یک وظیفه متوقف شد؛ وظیفه دیگر در دسترس نیست یا به این اتصال تعلق ندارد.\n"
                f"شناسه: {watch.task_id}"
            )
            try:
                await _send_response(api, watch.telegram_user_id, message)
            except TelegramAPIError:
                LOGGER.warning("Task-watch cleanup notification failed or has unknown outcome")
                _defer_task_watch(store, watch, current_time)
                continue
            try:
                store.remove(watch.task_id, watch.telegram_user_id)
            except TaskWatchError:
                LOGGER.warning("Task-watch cleanup state could not be persisted")
                _defer_task_watch(store, watch, current_time)
            continue

        if error_code is not None:
            LOGGER.warning("Hub task-watch lookup failed; retry scheduled")
            _defer_task_watch(store, watch, current_time)
            continue
        if not isinstance(task, dict):
            LOGGER.warning("Hub task-watch response was invalid; retry scheduled")
            _defer_task_watch(store, watch, current_time)
            continue
        status = task.get("status")
        if not isinstance(status, str) or status not in TASK_STATUSES:
            LOGGER.warning("Hub task-watch response had an invalid status; retry scheduled")
            _defer_task_watch(store, watch, current_time)
            continue
        if status == watch.last_status:
            if watch.retry_count:
                try:
                    store.clear_retry(watch.task_id, watch.telegram_user_id)
                except TaskWatchError:
                    LOGGER.warning("Task-watch retry state could not be cleared")
            continue

        history = None
        history_reader = getattr(hub, "get_task_history", None)
        if callable(history_reader):
            try:
                history = await history_reader(
                    watch.telegram_user_id, watch.task_id, limit=5
                )
            except Exception:  # noqa: BLE001 - history is optional; status delivery must continue
                LOGGER.warning("Task-watch history lookup failed; sending status-only progress")
        message = _format_task_progress(task, history, watch.task_id)
        try:
            await _send_response(api, watch.telegram_user_id, message)
        except TelegramAPIError:
            LOGGER.warning("Telegram task-watch notification failed or has unknown outcome")
            _defer_task_watch(store, watch, current_time)
            continue
        try:
            if status in TERMINAL_TASK_STATUSES:
                store.remove(watch.task_id, watch.telegram_user_id)
            else:
                store.update_status(watch.task_id, watch.telegram_user_id, status)
        except TaskWatchError:
            LOGGER.warning("Telegram task-watch checkpoint could not be persisted")
            _defer_task_watch(store, watch, current_time)


async def _owner_hub_command(
    text: str,
    *,
    telegram_user_id: int,
    update_id: int,
    hub: HubClient | None,
    settings: Settings,
    task_watches: TaskWatchStore | None = None,
    task_plans: TaskPlanStore | None = None,
    api: TelegramBotAPI | None = None,
    chat_id: int | None = None,
) -> str | None:
    parts = text.strip().split(maxsplit=1)
    command = parts[0].split("@", 1)[0].lower()
    argument = parts[1].strip() if len(parts) > 1 else ""

    if command == "/doctor":
        if argument:
            return "روش استفاده: /doctor"
        from el_ai.doctor import collect_report, render_text

        return render_text(collect_report())

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

    if command not in {
        "/hub", "/tasks", "/memory", "/memoryentry", "/memoryapprove", "/memorydream", "/memorycheck", "/task", "/taskstatus", "/taskprogress", "/taskplan", "/agentplan", "/taskplanrun", "/taskplans", "/taskplanstatus", "/taskplanwatch", "/taskplancancel", "/taskplanpause", "/taskplanresume", "/taskplanspauseall", "/taskplansresumeall", "/taskplanevaluate", "/taskresult", "/taskcancel",
        "/taskhistory", "/taskwatch", "/taskunwatch", "/taskwatches",
        "/videoproviders", "/videoroute", "/videohistory", "/videostatus", "/videocancel",
        "/videodrafts", "/videorenders", "/videorendercheck", "/videorender", "/videorenderstatus",
        "/videorenderdownload", "/videorendercancel",
    "/videoscenes", "/videoscenestatus", "/videoscenecancel",
    "/videoscenehistory", "/videosceneexport",
    }:
        return None
    if command in {"/taskplanspauseall", "/taskplansresumeall"}:
        if task_plans is None:
            return "ذخیره‌سازی طرح وظایف در دسترس نیست؛ هیچ تغییری انجام نشد."
        if argument.upper() != "CONFIRM":
            return (
                "برای مکث همهٔ طرح‌های مالک، دستور دقیق را با تأیید بفرست: "
                "/taskplanspauseall CONFIRM"
                if command == "/taskplanspauseall"
                else "برای ادامهٔ همهٔ طرح‌های مالک، دستور دقیق را با تأیید بفرست: /taskplansresumeall CONFIRM"
            )
        try:
            paused = command == "/taskplanspauseall"
            changed = task_plans.set_all_paused(telegram_user_id, paused)
        except (TaskPlanError, ValueError):
            return "تغییر وضعیت طرح‌ها ذخیره نشد؛ هیچ مرحلهٔ جدیدی ارسال نشد."
        if paused:
            return (
                f"کلید توقف طرح‌ها فعال شد؛ {changed} طرح مکث شد. "
                "مرحله‌های جدید از این ربات به Hub ارسال نمی‌شوند. "
                "وظایفی که قبلاً به Hub تحویل شده‌اند ادامه دارند و با این دستور متوقف نمی‌شوند."
            )
        return (
            f"کلید توقف غیرفعال شد؛ {changed} طرح از مکث خارج شد. "
            "ارسال مرحله‌های آماده در چرخهٔ بعدی هماهنگ‌ساز از سر گرفته می‌شود."
        )

    if not settings.hub_integration_enabled:
        return "اتصال Hub هنوز در تنظیمات این اجرا فعال نشده است."
    if hub is None:
        return "ذخیره‌ساز امن سیستم یا آدرس Hub در دسترس نیست."
    try:
        if command == "/hub":
            return _format_result(await hub.status(telegram_user_id))
        if command == "/tasks":
            parts = argument.split(maxsplit=1)
            status_filter = parts[0] if parts and parts[0] in TASK_STATUSES else None
            cursor = parts[1] if status_filter and len(parts) > 1 else (
                None if status_filter else (argument or None)
            )
            try:
                result = await hub.list_tasks(
                    telegram_user_id,
                    cursor=cursor,
                    limit=20,
                    status=status_filter,
                )
            except ValueError:
                return "فیلتر وضعیت یا نشانگر صفحهٔ وظایف نامعتبر است؛ /tasks را بدون شناسه اجرا کن."
            next_cursor = result.get("next_cursor")
            visible = {key: value for key, value in result.items() if key != "next_cursor"}
            rendered = _format_result(visible)
            if result.get("has_more") and isinstance(next_cursor, str) and next_cursor:
                next_command = "/tasks" + (f" {status_filter}" if status_filter else "") + f" {next_cursor}"
                return rendered + f"\n\nصفحه بعد: {next_command}"
            return rendered
        if command == "/memory":
            if not argument:
                return "روش استفاده: /memory عبارت جست‌وجو"
            return _format_result(await hub.search_memory(telegram_user_id, argument[:200]))
        if command == "/memoryentry":
            if not argument:
                return "روش استفاده: /memoryentry MEMORY_ID"
            try:
                memory_id = str(UUID(argument))
            except ValueError:
                return "شناسه یادداشت باید یک UUID معتبر باشد."
            try:
                return _format_result(await hub.get_memory_entry(telegram_user_id, memory_id))
            except HubClientError as exc:
                if exc.status_code == 404:
                    return "یادداشت پیدا نشد یا منقضی شده است."
                raise
        if command == "/memoryapprove":
            if not argument:
                return "روش استفاده: /memoryapprove MEMORY_ID — فقط پس از بازبینی یادداشت"
            try:
                memory_id = str(UUID(argument))
            except ValueError:
                return "شناسه یادداشت باید یک UUID معتبر باشد."
            try:
                result = await hub.approve_memory_entry(telegram_user_id, memory_id)
            except HubClientError as exc:
                if exc.status_code == 403:
                    return "مجوز memory:write برای این اتصال فعال نیست؛ مالک باید آن را جداگانه در پنل Hub اعطا کند."
                if exc.status_code == 404:
                    return "یادداشت پیدا نشد یا منقضی شده است."
                raise
            return (
                "یادداشت با تأیید صریح مالک علامت‌گذاری شد."
                if result.get("changed") is True
                else "این یادداشت قبلاً تأیید شده است."
            )
        if command == "/memorydream":
            if argument:
                return "این دستور فعلاً بدون آرگومان اجرا می‌شود: /memorydream"
            return _format_result(
                await hub.preview_memory_dream(telegram_user_id, limit=100)
            )
        if command == "/memorycheck":
            if argument:
                return "این دستور فعلاً بدون آرگومان اجرا می‌شود: /memorycheck"
            return _format_result(
                await hub.preview_memory_validation(telegram_user_id, limit=100)
            )
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
        if command == "/agentplan":
            if task_plans is None:
                return "ذخیره‌سازی طرح وظایف در دسترس نیست."
            if "|" not in argument:
                return "روش استفاده: /agentplan [local|remote] task.type | هدفی که باید به مراحل اجرایی تبدیل شود"
            header, goal = (part.strip() for part in argument.split("|", 1))
            options = header.split()
            provider_mode = "local"
            if options and options[0].lower() in {"local", "remote"}:
                provider_mode = options.pop(0).lower()
            if len(options) != 1 or not goal:
                return "روش استفاده: /agentplan [local|remote] task.type | هدف"
            if len(goal) > 1_000:
                return "هدف طرح حداکثر ۱۰۰۰ نویسه است؛ متن را کوتاه‌تر کن."
            task_type = options[0]
            try:
                result = await hub.preview_agent_plan(
                    telegram_user_id,
                    goal,
                    provider_mode=provider_mode,
                )
            except HubClientError as exc:
                if exc.status_code == 403:
                    return "مجوز tasks:create برای این اتصال فعال نیست؛ مالک باید آن را در پنل Hub اعطا کند."
                if exc.status_code == 429:
                    return "سهمیهٔ درخواست‌های ارائه‌دهنده موقتاً پر شده است؛ بعداً دوباره تلاش کن."
                if exc.status_code == 503:
                    return "ارائه‌دهندهٔ انتخاب‌شده در Hub در دسترس یا پیکربندی نشده است."
                if exc.status_code == 502:
                    return "مدل نتوانست یک طرح معتبر تولید کند؛ هدف را دقیق‌تر بنویس و دوباره تلاش کن."
                raise
            steps = result.get("steps")
            if (
                result.get("provider_mode") != provider_mode
                or result.get("execution_started") is not False
                or type(result.get("tasks_created")) is not int
                or result.get("tasks_created") != 0
                or not isinstance(steps, list)
            ):
                return "پاسخ Hub با قرارداد پیش‌نمایش امن سازگار نبود؛ هیچ طرحی ذخیره نشد."
            plan_contract = result.get("plan_contract")
            contract_note = ""
            if plan_contract is not None:
                if (
                    not isinstance(plan_contract, dict)
                    or not isinstance(plan_contract.get("steps"), list)
                    or len(plan_contract["steps"]) != len(steps)
                ):
                    return "قرارداد نسخه‌دار Hub با فهرست مراحل قدیمی سازگار نیست؛ هیچ طرحی ذخیره نشد."
                for legacy_step, contract_step in zip(steps, plan_contract["steps"]):
                    if (
                        not isinstance(legacy_step, dict)
                        or not isinstance(legacy_step.get("description"), str)
                        or not isinstance(contract_step, dict)
                        or not isinstance(contract_step.get("description"), str)
                        or legacy_step["description"] != contract_step["description"][:400]
                    ):
                        return "مراحل قرارداد Hub با پاسخ سازگاری مطابقت ندارند؛ هیچ طرحی ذخیره نشد."
                try:
                    plan = task_plans.create_from_contract(
                        telegram_user_id,
                        task_type,
                        plan_contract,
                    )
                except (TaskPlanError, ValueError):
                    return "قرارداد معیارهای پذیرش یا گراف مراحل نامعتبر است؛ هیچ طرحی ذخیره نشد."
                criteria_preview = "\n".join(
                    f"- {item.criterion_id}: {item.statement}"
                    for item in plan.acceptance_criteria
                )
                verification_preview = "\n".join(
                    f"- {item}" for item in plan.verification_steps
                )
                missing_preview = "\n".join(
                    f"- {item}" for item in plan_contract["missing_information"]
                )
                clarification_preview = "\n".join(
                    f"- {item}" for item in plan_contract["clarification_questions"]
                )
                research_preview = "\n".join(
                    f"- {item}" for item in plan_contract["research_questions"]
                )
                contract_note = (
                    f"\nقرارداد: {plan.contract_version} · وضعیت برنامه‌ریزی: {plan.planning_status}"
                    f"\nمعیارهای پذیرش (هنوز ارزیابی نشده‌اند):\n{criteria_preview}"
                    f"\nروش‌های راستی‌آزمایی پیشنهادی:\n{verification_preview}"
                    f"\nتوجه: وجود معیار و روش بررسی به‌معنای موفقیت آن نیست."
                )
                if plan.planning_status == "needs_clarification":
                    contract_note += (
                        f"\nاطلاعات ناقص:\n{missing_preview}"
                        f"\nپرسش‌های لازم برای شفاف‌سازی:\n{clarification_preview}"
                    )
                    run_instruction = (
                        "\nاین طرح تا زمان رفع ابهام و ساخت پیش‌نمایش جدید قابل اجرا نیست."
                    )
                elif plan.planning_status == "needs_research":
                    contract_note += f"\nپرسش‌های پژوهشی لازم:\n{research_preview}"
                    run_instruction = (
                        "\nاین طرح تا زمان تکمیل پژوهش و ساخت پیش‌نمایش جدید قابل اجرا نیست."
                    )
                else:
                    run_instruction = (
                        f"\nبرای بررسی و شروع صریح مراحل: /taskplanrun {plan.plan_id}"
                    )
            else:
                # Backward compatibility with older Hub instances that expose only descriptions.
                descriptions: list[str] = []
                dependencies: list[list[int]] | None = None
                has_dependency_metadata = any(
                    isinstance(item, dict) and "depends_on" in item for item in steps
                )
                parsed_dependencies: list[list[int]] = []
                for index, item in enumerate(steps):
                    if not isinstance(item, dict) or not isinstance(item.get("description"), str):
                        return "پاسخ مدل شامل مراحل نامعتبر بود؛ هیچ طرحی ذخیره نشد."
                    descriptions.append(item["description"])
                    if has_dependency_metadata:
                        raw_dependencies = item.get("depends_on", [] if index == 0 else [index])
                        if not isinstance(raw_dependencies, list):
                            return "گراف وابستگی مدل نامعتبر بود؛ هیچ طرحی ذخیره نشد."
                        converted: list[int] = []
                        for dependency in raw_dependencies:
                            if isinstance(dependency, bool) or not isinstance(dependency, int):
                                return "شماره پیش‌نیاز مدل نامعتبر بود؛ هیچ طرحی ذخیره نشد."
                            converted.append(dependency - 1)
                        parsed_dependencies.append(converted)
                if has_dependency_metadata:
                    dependencies = parsed_dependencies
                try:
                    plan = task_plans.create(
                        telegram_user_id, task_type, descriptions, dependencies=dependencies
                    )
                except (TaskPlanError, ValueError):
                    return "طرح تولیدشده یا گراف وابستگی آن معتبر نیست؛ هیچ طرحی ذخیره نشد."
                run_instruction = f"\nبرای ثبت صریح مراحل: /taskplanrun {plan.plan_id}"
            preview = "\n".join(
                f"{index + 1}. {step.description}"
                + (
                    " · معیارهای مرتبط: " + ", ".join(step.verifies_criteria)
                    if step.verifies_criteria else ""
                )
                + (f" · راستی‌آزمایی: {step.verification}" if step.verification else "")
                for index, step in enumerate(plan.steps)
            )
            provider = _safe_progress_value(result.get("provider"), limit=40) or "unknown"
            remote_note = (
                "\nحالت remote استفاده شد؛ ممکن است هزینهٔ ارائه‌دهنده اعمال شود."
                if provider_mode == "remote"
                else ""
            )
            return (
                "پیش‌نمایش هوشمند ذخیره شد؛ هیچ وظیفه‌ای ایجاد یا اجرا نشده است."
                f"\nProvider: {provider}"
                f"\nشناسه طرح: {plan.plan_id}"
                f"\nنوع: {plan.task_type}\n{preview}"
                f"{contract_note}"
                f"{run_instruction}"
                f"{remote_note}"
            )
        if command == "/taskplan":
            if task_plans is None:
                return "ذخیره‌سازی طرح وظایف در دسترس نیست."
            if "|" not in argument:
                return "روش استفاده: /taskplan task.type | مرحله اول ; مرحله دوم"
            task_type, raw_steps = (part.strip() for part in argument.split("|", 1))
            try:
                descriptions, dependencies = _parse_task_plan_spec(raw_steps)
                plan = task_plans.create(
                    telegram_user_id,
                    task_type,
                    descriptions,
                    dependencies=dependencies,
                )
            except (TaskPlanError, ValueError):
                return (
                    "طرح معتبر نیست؛ ۲ تا ۸ مرحله غیرخالی لازم است. برای وابستگی صریح از "
                    "«1: آماده‌سازی ; 2: بررسی <- 1 ; 3: انتشار <- 1,2» استفاده کن."
                )
            preview = "\n".join(
                f"{index + 1}. {step.description}"
                + (
                    " · پیش‌نیاز: " + ",".join(str(item + 1) for item in step.depends_on)
                    if step.depends_on else " · بدون پیش‌نیاز"
                )
                for index, step in enumerate(plan.steps)
            )
            return (
                "پیش‌نمایش گراف وظایف ذخیره شد؛ هنوز هیچ وظیفه‌ای به Hub ارسال نشده است.\n"
                f"شناسه طرح: {plan.plan_id}\n"
                f"نوع: {plan.task_type}\n{preview}\n"
                f"برای شروع صریح اجرا: /taskplanrun {plan.plan_id}\n"
                "فقط مراحل ریشه ابتدا ارسال می‌شوند؛ مراحل وابسته پس از موفقیت همه پیش‌نیازها به‌طور خودکار ثبت می‌شوند."
            )
        if command == "/taskplanpause":
            if task_plans is None:
                return "ذخیره‌سازی طرح وظایف در دسترس نیست."
            if not argument:
                return "روش استفاده: /taskplanpause PLAN_ID"
            try:
                plan_id = str(UUID(argument))
                plan = task_plans.get_for_user(plan_id, telegram_user_id)
            except (TaskPlanError, ValueError):
                return "شناسه طرح نامعتبر است."
            if plan is None:
                return "طرح پیدا نشد، منقضی شده یا متعلق به این اتصال ربات نیست."
            if plan.contract_version and plan.planning_status != "ready_for_review":
                return (
                    "این طرح هنوز آمادهٔ اجرا نیست. ابتدا ابهام‌ها یا پژوهش‌های لازم را "
                    "برطرف کن و یک پیش‌نمایش جدید بساز."
                )
            if plan.submitted_count == 0:
                return "این طرح هنوز شروع نشده است؛ ابتدا /taskplanrun را اجرا کن."
            try:
                changed = task_plans.set_paused(plan_id, telegram_user_id, True)
            except (TaskPlanError, ValueError):
                return "مکث طرح ذخیره نشد؛ وضعیت فایل خصوصی طرح را بررسی کن."
            if not changed:
                return "این طرح از قبل در حالت مکث است."
            return (
                "طرح در حالت مکث قرار گرفت. وظایفی که قبلاً به Hub ارسال شده‌اند ادامه دارند؛ "
                "فقط ثبت خودکار مراحل وابسته متوقف شد.\n"
                f"برای ادامهٔ صریح: /taskplanresume {plan_id} CONFIRM"
            )
        if command == "/taskplanresume":
            parts = argument.split()
            if len(parts) != 2 or parts[1].upper() != "CONFIRM":
                return "برای ادامهٔ طرح، تأیید صریح لازم است: /taskplanresume PLAN_ID CONFIRM"
            if task_plans is None:
                return "ذخیره‌سازی طرح وظایف در دسترس نیست."
            try:
                plan_id = str(UUID(parts[0]))
                plan = task_plans.get_for_user(plan_id, telegram_user_id)
            except (TaskPlanError, ValueError):
                return "شناسه طرح نامعتبر است."
            if plan is None:
                return "طرح پیدا نشد، منقضی شده یا متعلق به این اتصال ربات نیست."
            if not plan.paused:
                return "این طرح در حالت مکث نیست."
            try:
                changed = task_plans.set_paused(plan_id, telegram_user_id, False)
            except (TaskPlanError, ValueError):
                return "ادامهٔ طرح ذخیره نشد؛ وضعیت فایل خصوصی طرح را بررسی کن."
            if not changed:
                return "مکث طرح از قبل برداشته شده است."
            return (
                "مکث طرح برداشته شد. در نوبت بعدی پایش، فقط مراحلی که همهٔ پیش‌نیازهایشان "
                "با موفقیت تکمیل شده‌اند به Hub ارسال می‌شوند."
            )
        if command == "/taskplans":
            if task_plans is None:
                return "ذخیره‌سازی طرح وظایف در دسترس نیست."
            try:
                plans = task_plans.list_for_user(telegram_user_id)
            except (TaskPlanError, ValueError):
                return "فهرست طرح‌های ذخیره‌شده در دسترس نیست."
            return _format_result({
                "count": len(plans),
                "plans": [
                    {
                        "plan_id": plan.plan_id,
                        "task_type": plan.task_type,
                        "paused": plan.paused,
                        "submitted_steps": plan.submitted_count,
                        "blocked_steps": sum(bool(step.blocked_reason) for step in plan.steps),
                        "ready_root_steps": sum(
                            step.task_id is None and not step.blocked_reason and not step.depends_on
                            for step in plan.steps
                        ),
                        "total_steps": len(plan.steps),
                        "created_at": plan.created_at,
                    }
                    for plan in plans
                ],
            })
        if command == "/taskplanstatus":
            if task_plans is None:
                return "ذخیره‌سازی طرح وظایف در دسترس نیست."
            if not argument:
                return "روش استفاده: /taskplanstatus PLAN_ID"
            try:
                plan_id = str(UUID(argument))
                plan = task_plans.get_for_user(plan_id, telegram_user_id)
            except (TaskPlanError, ValueError):
                return "شناسه طرح نامعتبر است."
            if plan is None:
                return "طرح پیدا نشد، منقضی شده یا متعلق به این اتصال ربات نیست."
            lines = [
                "وضعیت مراحل طرح",
                f"شناسه طرح: {plan.plan_id}",
                f"ارسال‌شده به Hub: {plan.submitted_count} از {len(plan.steps)}",
                "وضعیت طرح: " + ("مکث‌شده" if plan.paused else ("شروع‌نشده" if plan.submitted_count == 0 else "فعال")),
            ]
            for index, step in enumerate(plan.steps):
                dependencies_label = (
                    " · پیش‌نیاز: " + ",".join(str(item + 1) for item in step.depends_on)
                    if step.depends_on else ""
                )
                if step.blocked_reason:
                    blocked_by = (
                        " · وابستگی ناموفق: " + ",".join(str(item + 1) for item in step.blocked_by)
                        if step.blocked_by else ""
                    )
                    lines.append(f"{index + 1}. مسدود ({step.blocked_reason}){blocked_by}{dependencies_label}")
                    continue
                if step.task_id is None:
                    label = "آماده ارسال" if not step.depends_on else "در انتظار موفقیت پیش‌نیازها"
                    lines.append(f"{index + 1}. {label}{dependencies_label}")
                    continue
                try:
                    task = await hub.get_task(telegram_user_id, step.task_id)
                except HubClientError:
                    lines.append(f"{index + 1}. وضعیت در دسترس نیست — {step.task_id}")
                    continue
                status = task.get("status") if isinstance(task, dict) else None
                status_label = (
                    _task_status_label(status)
                    if isinstance(status, str) and status in TASK_STATUSES
                    else "نامشخص"
                )
                lines.append(f"{index + 1}. {status_label} — {step.task_id}{dependencies_label}")
            return "\n".join(lines)
        if command == "/taskplanevaluate":
            if task_plans is None:
                return "ذخیره‌سازی طرح وظایف در دسترس نیست."
            if not argument:
                return "روش استفاده: /taskplanevaluate PLAN_ID"
            if hub is None:
                return "اتصال Hub برای خواندن شواهد نتایج در دسترس نیست."
            try:
                plan_id = str(UUID(argument))
                plan = task_plans.get_for_user(plan_id, telegram_user_id)
            except (TaskPlanError, ValueError):
                return "شناسه طرح نامعتبر است."
            if plan is None:
                return "طرح پیدا نشد، منقضی شده یا متعلق به این اتصال ربات نیست."
            report = await evaluate_task_plan_evidence(hub, telegram_user_id, plan)
            return _format_result(report.as_dict())
        if command == "/taskplanwatch":
            if task_plans is None or task_watches is None:
                return "ذخیره‌سازی طرح یا پایش وظایف در دسترس نیست."
            if not argument:
                return "روش استفاده: /taskplanwatch PLAN_ID"
            try:
                plan_id = str(UUID(argument))
                plan = task_plans.get_for_user(plan_id, telegram_user_id)
            except (TaskPlanError, ValueError):
                return "شناسه طرح نامعتبر است."
            if plan is None:
                return "طرح پیدا نشد، منقضی شده یا متعلق به این اتصال ربات نیست."
            submitted = [
                (index, step.task_id)
                for index, step in enumerate(plan.steps)
                if step.task_id is not None
            ]
            if not submitted:
                return "هنوز هیچ مرحله‌ای به Hub ارسال نشده است؛ ابتدا /taskplanrun را اجرا کن."

            semaphore = asyncio.Semaphore(4)

            async def read_plan_step(index: int, task_id: str):
                async with semaphore:
                    try:
                        task = await hub.get_task(telegram_user_id, task_id)
                    except HubClientError as exc:
                        return index, task_id, None, exc.status_code if exc.status_code is not None else 0
                status = task.get("status") if isinstance(task, dict) else None
                if not isinstance(status, str) or status not in TASK_STATUSES:
                    return index, task_id, None, 0
                return index, task_id, status, None

            results = await asyncio.gather(
                *(read_plan_step(index, task_id) for index, task_id in submitted)
            )
            active_entries = [
                (task_id, status)
                for _index, task_id, status, error in results
                if error is None and isinstance(status, str) and status not in TERMINAL_TASK_STATUSES
            ]
            terminal_count = sum(
                error is None and isinstance(status, str) and status in TERMINAL_TASK_STATUSES
                for _index, _task_id, status, error in results
            )
            unavailable_count = sum(error is not None for _index, _task_id, _status, error in results)
            if not active_entries:
                return (
                    "هیچ مرحله فعال و قابل‌پایشی پیدا نشد.\n"
                    f"مراحل نهایی: {terminal_count} · در دسترس نبود: {unavailable_count}"
                )
            try:
                added_ids = task_watches.add_many(active_entries, telegram_user_id)
            except (TaskWatchError, ValueError):
                return "ظرفیت پایش کافی نیست یا ذخیره‌سازی نامعتبر است؛ هیچ پایشی از این طرح ثبت نشد."
            already_watched = len(active_entries) - len(added_ids)
            return (
                "پایش گروهی طرح بررسی و ثبت شد.\n"
                f"شناسه طرح: {plan.plan_id}\n"
                f"پایش جدید: {len(added_ids)} · از قبل فعال: {already_watched}\n"
                f"مراحل نهایی: {terminal_count} · در دسترس نبود: {unavailable_count}\n"
                "اعلان هر مرحله پس از تغییر وضعیت جداگانه ارسال می‌شود."
            )
        if command == "/taskplancancel":
            parts = argument.split()
            if len(parts) != 2 or parts[1].upper() != "CONFIRM":
                return "برای لغو گروهی، دستور دقیق را با تأیید صریح بفرست: /taskplancancel PLAN_ID CONFIRM"
            if task_plans is None:
                return "ذخیره‌سازی طرح وظایف در دسترس نیست."
            try:
                plan_id = str(UUID(parts[0]))
                plan = task_plans.get_for_user(plan_id, telegram_user_id)
            except (TaskPlanError, ValueError):
                return "شناسه طرح نامعتبر است."
            if plan is None:
                return "طرح پیدا نشد، منقضی شده یا متعلق به این اتصال ربات نیست."
            submitted = [
                (index, step.task_id)
                for index, step in enumerate(plan.steps)
                if step.task_id is not None
            ]
            if not submitted:
                return "این طرح هنوز هیچ وظیفه‌ای در Hub ندارد."
            semaphore = asyncio.Semaphore(4)

            async def cancel_plan_step(index: int, task_id: str):
                async with semaphore:
                    try:
                        result = await hub.cancel_task(telegram_user_id, task_id)
                    except HubClientError as exc:
                        if exc.status_code == 409:
                            return index, task_id, "not_cancellable"
                        if exc.status_code == 404:
                            return index, task_id, "not_found"
                        return index, task_id, "unknown"
                status = result.get("status") if isinstance(result, dict) else None
                if status == "cancelled":
                    return index, task_id, "cancelled"
                if isinstance(status, str) and status in TASK_STATUSES:
                    return index, task_id, status
                return index, task_id, "unknown"

            results = await asyncio.gather(
                *(cancel_plan_step(index, task_id) for index, task_id in submitted)
            )
            lines = [
                "درخواست لغو گروهی طرح بررسی شد؛ وظایف درحال‌اجرا یا نهایی به‌زور متوقف نمی‌شوند.",
                f"شناسه طرح: {plan.plan_id}",
            ]
            cancelled_count = 0
            for index, task_id, outcome in results:
                if outcome == "cancelled":
                    label = "لغو شد"
                    cancelled_count += 1
                elif outcome == "not_cancellable":
                    label = "درحال‌اجرا یا نهایی؛ لغو نشد"
                elif outcome == "not_found":
                    label = "پیدا نشد یا متعلق به این اتصال نیست"
                elif outcome == "unknown":
                    label = "نتیجه نامشخص؛ وضعیت را دوباره بررسی کن"
                else:
                    label = "وضعیت: " + _task_status_label(outcome)
                lines.append(f"{index + 1}. {label} — {task_id}")
            lines.append(f"لغوشده: {cancelled_count} از {len(submitted)} مرحله")
            lines.append(f"وضعیت نهایی هر مرحله: /taskplanstatus {plan.plan_id}")
            return "\n".join(lines)
        if command == "/taskplanrun":
            if task_plans is None:
                return "ذخیره‌سازی طرح وظایف در دسترس نیست."
            if not argument:
                return "روش استفاده: /taskplanrun PLAN_ID"
            try:
                plan_id = str(UUID(argument))
                plan = task_plans.get_for_user(plan_id, telegram_user_id)
            except (TaskPlanError, ValueError):
                return "شناسه طرح نامعتبر است."
            if plan is None:
                return "طرح پیدا نشد، منقضی شده یا متعلق به این اتصال ربات نیست."
            if plan.contract_version and plan.planning_status != "ready_for_review":
                return (
                    "این طرح هنوز آمادهٔ اجرا نیست. ابتدا ابهام‌ها یا پژوهش‌های لازم را "
                    "برطرف کن و یک پیش‌نمایش جدید بساز."
                )
            if plan.paused:
                return f"این طرح در حالت مکث است؛ برای ادامهٔ صریح /taskplanresume {plan.plan_id} CONFIRM را بفرست."
            submitted_ids = [step.task_id for step in plan.steps]
            root_indices = [
                index for index, step in enumerate(plan.steps)
                if not step.depends_on and not step.blocked_reason
            ]
            if not root_indices:
                return "این طرح مرحله ریشهٔ قابل‌ارسال ندارد؛ وضعیت طرح را بررسی کن."
            for index in root_indices:
                step = plan.steps[index]
                if step.task_id is not None:
                    continue
                idempotency_key = f"telegram-plan-{plan.plan_id}-{index + 1}"
                try:
                    result = await hub.create_task(
                        telegram_user_id,
                        plan.task_type,
                        step.description,
                        idempotency_key=idempotency_key,
                    )
                except HubClientError:
                    submitted_count = sum(task_id is not None for task_id in submitted_ids)
                    return (
                        f"ثبت طرح در مرحله {index + 1} متوقف شد؛ "
                        f"{submitted_count} از {len(plan.steps)} مرحله ثبت محلی شده است.\n"
                        "پس از رفع خطا، همان دستور /taskplanrun را دوباره بفرست؛ "
                        "کلیدهای idempotency از ثبت تکراری جلوگیری می‌کنند."
                    )
                raw_task_id = result.get("task_id") if isinstance(result, dict) else None
                try:
                    task_id = str(UUID(str(raw_task_id)))
                except (ValueError, TypeError, AttributeError):
                    return (
                        f"پاسخ Hub برای مرحله {index + 1} شناسه معتبر نداشت. "
                        "همان دستور را دوباره بفرست تا نتیجه با همان کلید idempotency بازیابی شود."
                    )
                try:
                    task_plans.mark_submitted(
                        plan.plan_id, telegram_user_id, index, task_id
                    )
                except (TaskPlanError, ValueError):
                    return (
                        f"مرحله {index + 1} در Hub ثبت شد، اما checkpoint محلی ذخیره نشد. "
                        "همان دستور را دوباره بفرست؛ ثبت تکراری انجام نخواهد شد."
                    )
                submitted_ids[index] = task_id
            root_submitted = [
                (index, submitted_ids[index])
                for index in root_indices
                if submitted_ids[index] is not None
            ]
            if task_watches is not None and root_submitted:
                try:
                    task_watches.add_many(
                        [(task_id, "queued") for _index, task_id in root_submitted],
                        telegram_user_id,
                    )
                except (TaskWatchError, ValueError):
                    LOGGER.warning("Root task watches could not be registered for plan %s", plan.plan_id)
            lines = [
                "مراحل ریشهٔ طرح به Hub ارسال شدند؛ مراحل وابسته خودکار و فقط پس از موفقیت پیش‌نیازها ثبت می‌شوند.",
                f"شناسه طرح: {plan.plan_id}",
            ]
            lines.extend(
                f"{index + 1}. {task_id}"
                for index, task_id in root_submitted
            )
            lines.append("برای مشاهدهٔ گراف و وضعیت همه مراحل: /taskplanstatus " + plan.plan_id)
            if task_watches is not None:
                lines.append("برای دریافت اعلان تغییر وضعیت: /taskplanwatch " + plan.plan_id)
            return "\n".join(lines)
        if command == "/taskstatus":
            if not argument:
                return "روش استفاده: /taskstatus TASK_ID"
            try:
                task_id = str(UUID(argument))
            except ValueError:
                return "شناسه وظیفه باید یک UUID معتبر باشد."
            return _format_result(await hub.get_task(telegram_user_id, task_id))
        if command == "/taskprogress":
            if not argument:
                return "روش استفاده: /taskprogress TASK_ID"
            try:
                task_id = str(UUID(argument))
            except ValueError:
                return "شناسه وظیفه باید یک UUID معتبر باشد."
            try:
                task = await hub.get_task(telegram_user_id, task_id)
            except HubClientError as exc:
                if exc.status_code == 404:
                    return "وظیفه پیدا نشد یا متعلق به این اتصال ربات نیست."
                raise
            try:
                history = await hub.get_task_history(
                    telegram_user_id, task_id, limit=10
                )
            except HubClientError as exc:
                if exc.status_code == 404:
                    return "وظیفه پیدا نشد یا متعلق به این اتصال ربات نیست."
                history = None
            return _format_task_progress(task, history, task_id)
        if command == "/taskhistory":
            if not argument:
                return "روش استفاده: /taskhistory TASK_ID [CURSOR]"
            parts = argument.split(maxsplit=1)
            try:
                task_id = str(UUID(parts[0]))
            except ValueError:
                return "شناسه وظیفه باید یک UUID معتبر باشد."
            cursor = parts[1] if len(parts) > 1 else None
            try:
                result = await hub.get_task_history(
                    telegram_user_id, task_id, limit=20, cursor=cursor
                )
            except ValueError:
                return "نشانگر صفحه تاریخچه نامعتبر است."
            except HubClientError as exc:
                if exc.status_code == 404:
                    return "وظیفه پیدا نشد یا متعلق به این اتصال ربات نیست."
                raise
            next_cursor = result.get("next_cursor")
            visible = {key: value for key, value in result.items() if key != "next_cursor"}
            rendered = _format_result(visible)
            if result.get("has_more") and isinstance(next_cursor, str) and next_cursor:
                return rendered + f"\n\nصفحه بعد: /taskhistory {task_id} {next_cursor}"
            return rendered
        if command == "/taskwatch":
            if not argument:
                return "روش استفاده: /taskwatch TASK_ID"
            if task_watches is None:
                return "ذخیره‌سازی پایش وظایف در دسترس نیست."
            try:
                task_id = str(UUID(argument))
            except ValueError:
                return "شناسه وظیفه باید یک UUID معتبر باشد."
            try:
                task = await hub.get_task(telegram_user_id, task_id)
            except HubClientError as exc:
                if exc.status_code == 404:
                    return "وظیفه پیدا نشد یا متعلق به این اتصال ربات نیست."
                raise
            current_status = task.get("status")
            if not isinstance(current_status, str) or current_status not in TASK_STATUSES:
                return "Hub وضعیت معتبری برای این وظیفه برنگرداند."
            if current_status in TERMINAL_TASK_STATUSES:
                return "این وظیفه قبلاً به وضعیت نهایی رسیده است؛ پایشی ثبت نشد."
            try:
                added = task_watches.add(task_id, telegram_user_id, current_status)
            except (TaskWatchError, ValueError):
                return "ثبت پایش انجام نشد؛ ظرفیت ذخیره‌سازی را بررسی کن."
            if not added:
                return "پایش این وظیفه از قبل فعال است."
            return (
                "پایش وضعیت وظیفه فعال شد. هنگام تغییر وضعیت، پیام جداگانه دریافت می‌کنی.\n"
                f"شناسه: {task_id}\nوضعیت فعلی: {_task_status_label(current_status)}"
            )
        if command == "/taskunwatch":
            if not argument:
                return "روش استفاده: /taskunwatch TASK_ID"
            if task_watches is None:
                return "ذخیره‌سازی پایش وظایف در دسترس نیست."
            try:
                task_id = str(UUID(argument))
            except ValueError:
                return "شناسه وظیفه باید یک UUID معتبر باشد."
            try:
                removed = task_watches.remove(task_id, telegram_user_id)
            except (TaskWatchError, ValueError):
                return "توقف پایش انجام نشد؛ وضعیت ذخیره‌سازی را بررسی کن."
            return "پایش وظیفه متوقف شد." if removed else "برای این وظیفه پایش فعالی ثبت نشده است."
        if command == "/taskwatches":
            if task_watches is None:
                return "ذخیره‌سازی پایش وظایف در دسترس نیست."
            try:
                watches = task_watches.list_for_user(telegram_user_id)
            except TaskWatchError:
                return "فهرست پایش‌ها در دسترس نیست."
            return _format_result({
                "count": len(watches),
                "watches": [
                    {"task_id": item.task_id, "status": item.last_status}
                    for item in watches
                ],
            })
        if command == "/taskresult":
            if not argument:
                return "روش استفاده: /taskresult TASK_ID"
            try:
                task_id = str(UUID(argument))
            except ValueError:
                return "شناسه وظیفه باید یک UUID معتبر باشد."
            try:
                return _format_result(await hub.get_task_result(telegram_user_id, task_id))
            except HubClientError as exc:
                if exc.status_code == 409:
                    return "نتیجه هنوز آماده نیست؛ ابتدا وضعیت را با /taskstatus بررسی کن."
                if exc.status_code == 413:
                    return "نتیجه از حد مجاز نمایش در تلگرام بزرگ‌تر است."
                raise
        if command == "/taskcancel":
            if not argument:
                return "روش استفاده: /taskcancel TASK_ID"
            try:
                task_id = str(UUID(argument))
            except ValueError:
                return "شناسه وظیفه باید یک UUID معتبر باشد."
            try:
                return _format_result(await hub.cancel_task(telegram_user_id, task_id))
            except HubClientError as exc:
                if exc.status_code == 409:
                    return "فقط وظایف در صف یا منتظر تأیید قابل لغو هستند؛ وظیفه اجراشده یا نهایی لغو نمی‌شود."
                if exc.status_code == 404:
                    return "وظیفه پیدا نشد یا متعلق به این اتصال ربات نیست."
                raise
        if command == "/videoproviders":
            return _format_result(await hub.list_video_providers(telegram_user_id))
        if command == "/videoroute":
            arguments = argument.split()
            usage = (
                "روش استفاده: /videoroute SECONDS ASPECT [720p|1080p] "
                "[remote-ok spend-ok USD_CAP] — فقط پیش‌نمایش؛ تولید شروع نمی‌شود."
            )
            if len(arguments) not in {2, 3, 6}:
                return usage
            if not arguments[0].isascii() or not arguments[0].isdigit():
                return usage
            duration = int(arguments[0])
            aspect_ratio = arguments[1]
            resolution = arguments[2] if len(arguments) >= 3 else "720p"
            remote_processing_approved = False
            remote_spend_approved = False
            max_remote_cost_usd = 0.0
            if len(arguments) == 6:
                if arguments[3:5] != ["remote-ok", "spend-ok"]:
                    return usage
                try:
                    max_remote_cost_usd = float(arguments[5])
                except ValueError:
                    return "سقف هزینه باید یک عدد معتبر بر حسب دلار باشد."
                if (
                    not math.isfinite(max_remote_cost_usd)
                    or not 0 <= max_remote_cost_usd <= 10_000
                ):
                    return "سقف هزینه باید بین صفر تا ۱۰۰۰۰ دلار باشد."
                remote_processing_approved = True
                remote_spend_approved = True
            try:
                result = await hub.preview_video_route(
                    telegram_user_id,
                    duration_seconds=duration,
                    aspect_ratio=aspect_ratio,
                    resolution=resolution,
                    remote_processing_approved=remote_processing_approved,
                    remote_spend_approved=remote_spend_approved,
                    max_remote_cost_usd=max_remote_cost_usd,
                )
            except ValueError:
                return usage
            return _format_result(result)
        if command == "/videohistory":
            try:
                result = await hub.list_video_generations(
                    telegram_user_id, cursor=argument or None, limit=10
                )
            except ValueError:
                return "نشانگر صفحه تاریخچه ویدئو نامعتبر است؛ /videohistory را بدون شناسه اجرا کن."
            next_cursor = result.get("next_cursor")
            visible = {key: value for key, value in result.items() if key != "next_cursor"}
            rendered = _format_result(visible)
            if result.get("has_more") and isinstance(next_cursor, str) and next_cursor:
                return rendered + f"\n\nصفحه بعد: /videohistory {next_cursor}"
            return rendered
        if command == "/videostatus":
            if not argument:
                return "روش استفاده: /videostatus GENERATION_ID"
            try:
                generation_id = str(UUID(argument))
            except ValueError:
                return "شناسه تولید ویدئو باید یک UUID معتبر باشد."
            try:
                return _format_result(
                    await hub.get_video_generation(telegram_user_id, generation_id)
                )
            except HubClientError as exc:
                if exc.status_code == 404:
                    return "تولید ویدئو پیدا نشد یا متعلق به حساب متصل نیست."
                raise
        if command == "/videocancel":
            if not argument:
                return "روش استفاده: /videocancel GENERATION_ID"
            try:
                generation_id = str(UUID(argument))
            except ValueError:
                return "شناسه تولید ویدئو باید یک UUID معتبر باشد."
            try:
                return _format_result(
                    await hub.cancel_video_generation(telegram_user_id, generation_id)
                )
            except HubClientError as exc:
                if exc.status_code == 409:
                    return "فقط تولیدهای آماده یا در صف، پیش از ارسال به ارائه‌دهنده، قابل لغو هستند."
                if exc.status_code == 404:
                    return "تولید ویدئو پیدا نشد یا متعلق به حساب متصل نیست."
                raise
        if command == "/videodrafts":
            if not argument:
                return "روش استفاده: /videodrafts ANALYSIS_ID"
            try:
                analysis_id = str(UUID(argument))
            except ValueError:
                return "شناسه تحلیل ویدئو باید یک UUID معتبر باشد."
            try:
                result = await hub.list_video_render_drafts(telegram_user_id, analysis_id)
            except HubClientError as exc:
                if exc.status_code == 404:
                    return "تحلیل ویدئو پیدا نشد یا متعلق به حساب متصل نیست."
                if exc.status_code == 409:
                    return "تحلیل باید ابتدا با موفقیت تکمیل شود."
                raise
            return _format_result({
                **result,
                "render_kind": "deterministic_ffmpeg",
                "note": "فقط Draftهای آماده‌شده قابل رندر هستند؛ آماده‌سازی Timeline در داشبورد Hub انجام می‌شود.",
            })

        if command == "/videorenders":
            try:
                result = await hub.list_video_renders(
                    telegram_user_id, limit=10, cursor=argument or None
                )
            except ValueError:
                return "نشانگر صفحه‌بندی رندر معتبر نیست؛ مقدار دریافتی از Hub را بدون تغییر ارسال کن."
            except HubClientError as exc:
                if exc.status_code == 422:
                    return "نشانگر صفحه‌بندی رندر منقضی یا نامعتبر است؛ فهرست را از صفحه اول باز کن."
                raise
            return _format_result({
                **result,
                "render_kind": "deterministic_ffmpeg",
            })

        if command == "/videorendercheck":
            if not argument:
                return "روش استفاده: /videorendercheck DRAFT_ID"
            try:
                draft_id = str(UUID(argument))
            except ValueError:
                return "شناسه Draft باید یک UUID معتبر باشد."
            try:
                result = await hub.preflight_video_render(telegram_user_id, draft_id)
            except HubClientError as exc:
                if exc.status_code == 404:
                    return "Draft پیدا نشد یا متعلق به حساب متصل نیست."
                if exc.status_code == 422:
                    return "شناسه Draft یا وضعیت Timeline معتبر نیست."
                raise
            checks = result.get("checks")
            if not isinstance(checks, list):
                return "Hub نتیجه معتبرِ پیش‌بررسی برنگرداند."
            labels = {
                "timeline_integrity": "یکپارچگی Timeline",
                "analysis_completed": "تکمیل تحلیل",
                "source_bounds": "محدوده و مالکیت منابع",
                "source_files": "فایل‌های منبع",
                "ffmpeg_available": "FFmpeg",
                "ffprobe_available": "FFprobe",
                "output_storage": "ذخیره‌سازی خروجی",
                "disk_space": "فضای دیسک",
            }
            statuses = {"pass": "موفق", "warning": "هشدار", "fail": "ناموفق"}
            lines = [
                "پیش‌بررسی رندر محلی",
                "وضعیت: " + ("آماده برای ثبت جداگانه" if result.get("ready") is True else "نیازمند رفع مانع"),
                "هیچ Job، رندر یا درخواست Provider آغاز نشد."
            ]
            for check in checks[:12]:
                if not isinstance(check, dict):
                    continue
                code = check.get("code")
                status = check.get("status")
                detail = check.get("detail")
                lines.append(
                    (labels.get(code, str(code)[:60]))
                    + ": " + statuses.get(status, "نامشخص")
                    + (" — " + detail[:180] if isinstance(detail, str) and detail else "")
                )
            if result.get("warnings"):
                lines.append("هشدارها مانع قطعی نیستند؛ Worker پیش از اجرا دوباره بررسی می‌کند.")
            lines.append("این نتیجه لحظه‌ای است و جایگزین بررسی نهایی Worker نمی‌شود.")
            return "\n".join(lines)
        if command == "/videorender":
            if not argument:
                return "روش استفاده: /videorender DRAFT_ID"
            try:
                draft_id = str(UUID(argument))
            except ValueError:
                return "شناسه Draft باید یک UUID معتبر باشد."
            try:
                readiness = await hub.preflight_video_render_for_submit(
                    telegram_user_id, draft_id
                )
            except HubClientError as exc:
                if exc.status_code == 404:
                    return "Draft پیدا نشد یا متعلق به حساب متصل نیست."
                if exc.status_code == 422:
                    return "Timeline برای رندر آماده نیست."
                raise
            if readiness.get("ready") is not True:
                failed_checks = [
                    item for item in readiness.get("checks", [])
                    if isinstance(item, dict) and item.get("status") == "fail"
                ]
                lines = ["رندر ثبت نشد؛ پیش‌بررسی محلی مانع اجرا شد."]
                for item in failed_checks[:8]:
                    lines.append(
                        str(item.get("code", "check"))[:60]
                        + " — "
                        + str(item.get("detail", "پیش‌نیاز ناموفق"))[:180]
                    )
                lines.append("هیچ Job یا پردازش Provider ایجاد نشد.")
                return "\n".join(lines)
            idempotency_key = f"el-ai:{telegram_user_id}:{update_id}:video-render"
            try:
                result = await hub.submit_video_render(
                    telegram_user_id,
                    draft_id,
                    idempotency_key=idempotency_key,
                )
            except HubClientError as exc:
                if exc.status_code == 404:
                    return "Draft پیدا نشد یا متعلق به حساب متصل نیست."
                if exc.status_code == 409:
                    return "Timeline این Draft هنوز برای رندر آماده نشده یا نسخه آن تغییر کرده است؛ ابتدا آن را در داشبورد Hub آماده و ذخیره کن."
                if exc.status_code == 422:
                    return "Timeline یا کلید درخواست رندر معتبر نیست؛ Draft را در Hub دوباره بررسی کن."
                raise
            return _format_result({
                **result,
                "render_kind": "deterministic_ffmpeg",
                "preflight_warnings": readiness.get("warnings", []),
                "note": "این رندر قطعی و محلی با FFmpeg است؛ مدل مولد یا سرویس ابری فراخوانی نمی‌شود. فایل خروجی در داشبورد Hub قابل دانلود است.",
            })
        if command == "/videorenderstatus":
            if not argument:
                return "روش استفاده: /videorenderstatus RENDER_ID"
            try:
                render_id = str(UUID(argument))
            except ValueError:
                return "شناسه رندر باید یک UUID معتبر باشد."
            try:
                result = await hub.get_video_render(telegram_user_id, render_id)
            except HubClientError as exc:
                if exc.status_code == 404:
                    return "رندر پیدا نشد یا متعلق به حساب متصل نیست."
                raise
            rendered = _format_result(result)
            if result.get("output_available") is True:
                rendered += "\n\nفایل MP4 خصوصی در داشبورد Hub قابل دانلود است."
            return rendered

        if command == "/videorenderdownload":
            if not argument:
                return "روش استفاده: /videorenderdownload RENDER_ID"
            try:
                render_id = str(UUID(argument))
            except ValueError:
                return "شناسه رندر باید یک UUID معتبر باشد."
            if api is None or chat_id is None:
                return "ارسال فایل در این محیط تلگرام فعال نیست."
            try:
                video = await hub.download_video_render(telegram_user_id, render_id)
            except HubClientError as exc:
                if exc.status_code == 404:
                    return "رندر پیدا نشد یا متعلق به حساب متصل نیست."
                if exc.status_code == 409:
                    return "رندر هنوز تکمیل نشده است؛ پس از وضعیت completed دوباره تلاش کن."
                if exc.status_code == 413:
                    return "حجم فایل از سقف ارسال تلگرام بیشتر است؛ آن را از داشبورد خصوصی Hub دریافت کن."
                raise
            await api.send_video(
                chat_id,
                video,
                filename=f"render-{render_id}.mp4",
                caption="رندر قطعی محلی FFmpeg · فایل خصوصی حساب متصل",
            )
            return "فایل MP4 آماده و در همین گفت‌وگوی خصوصی ارسال شد."
        if command == "/videorendercancel":
            if not argument:
                return "روش استفاده: /videorendercancel RENDER_ID"
            try:
                render_id = str(UUID(argument))
            except ValueError:
                return "شناسه رندر باید یک UUID معتبر باشد."
            try:
                return _format_result(await hub.cancel_video_render(telegram_user_id, render_id))
            except HubClientError as exc:
                if exc.status_code == 404:
                    return "رندر پیدا نشد یا متعلق به حساب متصل نیست."
                raise

        if command == "/videoscenes":
            parts = argument.split()
            if not parts or len(parts) > 3:
                return "روش استفاده: /videoscenes ASSET_ID [THRESHOLD] [MAX_CHANGES]"
            try:
                asset_id = str(UUID(parts[0]))
                threshold = float(parts[1]) if len(parts) >= 2 else 0.30
                max_changes = int(parts[2]) if len(parts) >= 3 else 24
            except ValueError:
                return "شناسه فایل، آستانه یا تعداد برش‌ها معتبر نیست."
            if not 0.05 <= threshold <= 0.95:
                return "آستانه تشخیص باید بین 0.05 و 0.95 باشد."
            if not 1 <= max_changes <= 24:
                return "حداکثر تعداد برش باید بین 1 و 24 باشد."
            try:
                result = await hub.submit_video_scene_job(
                    telegram_user_id,
                    asset_id,
                    threshold=threshold,
                    max_scene_changes=max_changes,
                    idempotency_key=f"el-ai:{telegram_user_id}:{update_id}:video-scene",
                )
            except HubClientError as exc:
                if exc.status_code == 403:
                    return "مجوز video:analyze برای این ربات فعال نیست؛ مالک باید آن را صریحاً در مجوزهای اتصال Hub اعطا کند."
                if exc.status_code == 404:
                    return "فایل ویدئو پیدا نشد یا متعلق به حساب متصل نیست."
                if exc.status_code == 415:
                    return "فقط فایل MP4 یا QuickTime برای تشخیص برش پشتیبانی می‌شود."
                if exc.status_code == 429:
                    return "ظرفیت تحلیل صحنه موقتاً پر است؛ بعداً دوباره تلاش کن."
                raise
            return _format_result({
                **result,
                "analysis_kind": "local_ffmpeg_scene_cut_detection",
                "note": "فقط زمان‌های احتمالی برش صحنه استخراج می‌شوند؛ تحلیل معنایی یا Provider خارجی اجرا نمی‌شود.",
            })

        if command == "/videoscenestatus":
            if not argument:
                return "روش استفاده: /videoscenestatus JOB_ID"
            try:
                job_id = str(UUID(argument))
            except ValueError:
                return "شناسه تحلیل صحنه باید یک UUID معتبر باشد."
            try:
                return _format_result(await hub.get_video_scene_job(telegram_user_id, job_id))
            except HubClientError as exc:
                if exc.status_code == 403:
                    return "مجوز video:read برای این ربات فعال نیست."
                if exc.status_code == 404:
                    return "تحلیل صحنه پیدا نشد یا متعلق به حساب متصل نیست."
                raise

        if command == "/videoscenecancel":
            if not argument:
                return "روش استفاده: /videoscenecancel JOB_ID"
            try:
                job_id = str(UUID(argument))
            except ValueError:
                return "شناسه تحلیل صحنه باید یک UUID معتبر باشد."
            try:
                return _format_result(await hub.cancel_video_scene_job(telegram_user_id, job_id))
            except HubClientError as exc:
                if exc.status_code == 403:
                    return "مجوز video:cancel برای این ربات فعال نیست."
                if exc.status_code == 404:
                    return "تحلیل صحنه پیدا نشد یا متعلق به حساب متصل نیست."
                if exc.status_code == 409:
                    return "وضعیت تحلیل تغییر کرده یا عملیات قبلاً پایان یافته است."
                raise

        if command == "/videoscenehistory":
            try:
                page = await hub.list_video_scene_jobs(
                    telegram_user_id,
                    limit=10,
                    cursor=argument or None,
                )
            except HubClientError as exc:
                if exc.status_code == 403:
                    return "مجوز video:read برای این ربات فعال نیست."
                raise
            return _format_result({
                "items": page.get("items", []),
                "count": page.get("count", 0),
                "has_more": page.get("has_more", False),
                "next_cursor": page.get("next_cursor"),
                "hint": "برای صفحه بعدی، /videoscenehistory را همراه next_cursor اجرا کن.",
            })

        if command == "/videosceneexport":
            if not argument:
                return "روش استفاده: /videosceneexport JOB_ID"
            try:
                job_id = str(UUID(argument))
            except ValueError:
                return "شناسه تحلیل صحنه باید یک UUID معتبر باشد."
            try:
                job = await hub.get_video_scene_job(telegram_user_id, job_id)
            except HubClientError as exc:
                if exc.status_code == 403:
                    return "مجوز video:read برای این ربات فعال نیست."
                if exc.status_code == 404:
                    return "تحلیل صحنه پیدا نشد یا متعلق به حساب متصل نیست."
                raise
            result = job.get("result")
            starts = result.get("scene_start_times") if isinstance(result, dict) else None
            if job.get("status") != "succeeded" or not isinstance(starts, list):
                return "خروجی CSV فقط پس از موفقیت تحلیل در دسترس است."
            rows = ["scene_index,start_seconds"]
            for index, value in enumerate(starts, start=1):
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    return "داده زمان‌بندی خروجی معتبر نیست."
                seconds = float(value)
                if not 0 <= seconds <= 60:
                    return "داده زمان‌بندی خروجی خارج از محدوده مجاز است."
                rows.append(f"{index},{seconds:.3f}")
            csv_content = "\ufeff" + "\n".join(rows) + "\n"
            if api is not None and chat_id is not None:
                await api.send_document(
                    chat_id,
                    csv_content.encode("utf-8"),
                    filename=f"scene-times-{job_id[:8]}.csv",
                    caption=f"زمان‌های شروع صحنه · {job_id[:8]}",
                )
                return ""
            return "CSV زمان‌های شروع صحنه:\n" + "\n".join(rows)
    except HubClientError:
        return "درخواست Hub ناموفق بود یا مجوز لازم وجود ندارد. وضعیت اتصال و Scopeها را بررسی کن."
    return None

PUBLIC_BOT_COMMANDS = (
    {"command": "start", "description": "شروع گفتگو"},
    {"command": "help", "description": "راهنمای امکانات"},
)
OWNER_BOT_COMMANDS = (
    *PUBLIC_BOT_COMMANDS,
    {"command": "owner", "description": "بررسی دسترسی مالک"},
    {"command": "doctor", "description": "بررسی امن تنظیمات و آمادگی اجرا"},
    {"command": "pairinfo", "description": "شناسه اتصال امن Hub"},
    {"command": "pair", "description": "اتصال امن به Hub"},
    {"command": "hub", "description": "وضعیت اتصال Hub"},
    {"command": "tasks", "description": "فهرست وظایف"},
    {"command": "memory", "description": "جست‌وجوی حافظه"},
    {"command": "memoryentry", "description": "مشاهده یک یادداشت حافظه"},
    {"command": "memoryapprove", "description": "تأیید صریح یک یادداشت حافظه"},
    {"command": "memorydream", "description": "بازبینی تکراری‌های حافظه"},
    {"command": "memorycheck", "description": "اعتبارسنجی و بررسی تناقض حافظه"},
    {"command": "task", "description": "ثبت وظیفه"},
    {"command": "taskstatus", "description": "وضعیت یک وظیفه"},
    {"command": "taskprogress", "description": "گزارش پیشرفت و رویدادهای اخیر وظیفه"},
    {"command": "taskplan", "description": "ساخت پیش‌نمایش طرح چندمرحله‌ای"},
    {"command": "agentplan", "description": "ساخت طرح چندمرحله‌ای با هوش مصنوعی"},
    {"command": "taskplanrun", "description": "ثبت صریح مراحل طرح در Hub"},
    {"command": "taskplans", "description": "فهرست طرح‌های ذخیره‌شده"},
    {"command": "taskplanstatus", "description": "وضعیت همه مراحل یک طرح"},
    {"command": "taskplanwatch", "description": "پایش گروهی مراحل فعال یک طرح"},
    {"command": "taskplancancel", "description": "لغو گروهی مراحل لغوپذیر یک طرح"},
    {"command": "taskplanpause", "description": "مکث ارسال مراحل جدید یک طرح"},
    {"command": "taskplanresume", "description": "ادامه صریح طرح مکث‌شده"},
    {"command": "taskplanspauseall", "description": "توقف ارسال مرحله‌های همه طرح‌های مالک"},
    {"command": "taskplansresumeall", "description": "ادامه ارسال مرحله‌های طرح‌های مالک"},
    {"command": "taskplanevaluate", "description": "ارزیابی فقط‌خواندنی شواهد نتایج طرح"},
    {"command": "taskhistory", "description": "تاریخچه صفحه‌بندی‌شده یک وظیفه"},
    {"command": "taskwatch", "description": "اعلان تغییر وضعیت وظیفه"},
    {"command": "taskunwatch", "description": "توقف اعلان‌های وظیفه"},
    {"command": "taskwatches", "description": "فهرست پایش‌های فعال"},
    {"command": "taskresult", "description": "نتیجه نهایی وظیفه"},
    {"command": "taskcancel", "description": "لغو وظیفه در صف"},
    {"command": "videoproviders", "description": "وضعیت ارائه‌دهندگان ویدئو"},
    {"command": "videoroute", "description": "پیش‌نمایش مسیر امن تولید ویدئو"},
    {"command": "videohistory", "description": "تاریخچه تولید ویدئو"},
    {"command": "videostatus", "description": "وضعیت یک تولید ویدئو"},
    {"command": "videocancel", "description": "لغو تولید ویدئوی در صف"},
    {"command": "videodrafts", "description": "فهرست Draftهای آماده رندر"},
    {"command": "videorenders", "description": "تاریخچه رندرهای محلی"},
    {"command": "videorendercheck", "description": "بررسی آمادگی رندر محلی"},
    {"command": "videorender", "description": "ثبت رندر قطعی محلی FFmpeg"},
    {"command": "videorenderstatus", "description": "وضعیت رندر محلی"},
    {"command": "videorenderdownload", "description": "دریافت فایل MP4 در تلگرام"},
    {"command": "videorendercancel", "description": "لغو رندر محلی"},
    {"command": "videoscenes", "description": "شروع تحلیل برش‌های ویدئو"},
    {"command": "videoscenestatus", "description": "وضعیت تحلیل صحنه"},
    {"command": "videoscenecancel", "description": "لغو تحلیل صحنه"},
    {"command": "videoscenehistory", "description": "تاریخچه تحلیل صحنه"},
    {"command": "videosceneexport", "description": "خروجی CSV زمان‌های صحنه"},
    {"command": "health", "description": "سلامت و آمار ربات"},
    {"command": "site", "description": "پرسش از دانش سایت"},
)


async def _register_command_menu(api: TelegramBotAPI, owner_ids: set[int] | frozenset[int]) -> None:
    """Publish public commands globally and owner-only commands per private chat."""
    await api.set_my_commands(
        list(PUBLIC_BOT_COMMANDS),
        scope={"type": "all_private_chats"},
    )
    for owner_id in sorted(owner_ids):
        try:
            await api.set_my_commands(
                list(OWNER_BOT_COMMANDS),
                scope={"type": "chat", "chat_id": owner_id},
            )
        except TelegramAPIError:
            LOGGER.warning(
                "Owner-specific command menu registration failed; continuing with other chats"
            )


def _bridge_update_id(job: BridgeJob) -> int:
    """Convert the unique edge job UUID into a stable integer idempotency seed."""
    return UUID(job.job_id).int


def _bridge_job_command(job: BridgeJob, owner_ids: frozenset[int]) -> str | None:
    """Translate only the edge Worker's documented, owner-scoped bridge payloads."""
    if job.telegram_user_id not in owner_ids or job.chat_id != job.telegram_user_id:
        return None
    payload = job.payload
    command = payload.get("command")
    if not isinstance(command, str):
        return None
    if command in {"status", "tasks"} and set(payload) == {"command"}:
        return "/hub" if command == "status" else "/tasks"
    if command == "pair" and set(payload) == {"command", "pairing_code"}:
        code = payload.get("pairing_code")
        if (
            isinstance(code, str)
            and 32 <= len(code) <= 128
            and code == code.strip()
            and not any(character.isspace() for character in code)
        ):
            return f"/pair {code}"
    if command == "dispatch" and set(payload) == {"command", "text"}:
        text = payload.get("text")
        if not isinstance(text, str) or not text or text != text.strip():
            return None
        try:
            text.encode("utf-8")
            utf16_length = len(text.encode("utf-16-le")) // 2
        except UnicodeEncodeError:
            return None
        if utf16_length > 4096 or any(
            ord(character) < 0x20 or ord(character) == 0x7F
            for character in text
        ):
            return None
        command_parts = text.split(maxsplit=1)
        if not command_parts:
            return None
        command_name = command_parts[0].split("@", 1)[0].lower()
        if command_name not in BRIDGE_OWNER_COMMANDS:
            return None
        return text
    return None


async def _run_cloudflare_bridge(settings: Settings) -> None:
    """Run the Python-side consumer without starting Telegram long polling."""
    if not settings.telegram_bridge_url or not settings.telegram_bridge_shared_secret:
        raise SystemExit("Cloudflare bridge configuration is incomplete")
    if settings.telegram_transport != "cloudflare_bridge":
        raise SystemExit("Cloudflare bridge runtime was selected without bridge mode")

    try:
        client_id = load_or_create_client_id(settings.state_dir)
    except InstanceIdentityError as exc:
        raise SystemExit("EL_AI instance identity is unavailable") from exc

    task_plans = TaskPlanStore(settings.state_dir / "task-plans.json")
    try:
        task_plans.load()
    except TaskPlanError as exc:
        raise SystemExit("Stored Telegram task-plan state is invalid or inaccessible") from exc

    hub = _build_hub_client(settings, client_id)
    if hub is None:
        raise SystemExit(
            "Secure Hub credentials are unavailable; configure a trusted OS keyring before starting bridge mode"
        )

    bridge = TelegramBridgeClient(
        settings.telegram_bridge_url,
        settings.telegram_bridge_shared_secret,
        timeout_seconds=settings.telegram_bridge_timeout_seconds,
    )

    async def handle_job(job: BridgeJob) -> str:
        command_text = _bridge_job_command(job, settings.owner_ids)
        if command_text is None:
            return "درخواست نامعتبر یا فاقد مجوز است؛ فقط فرمان‌های مجاز مالک پذیرفته می‌شوند."
        response = await _owner_hub_command(
            command_text,
            telegram_user_id=job.telegram_user_id,
            update_id=_bridge_update_id(job),
            hub=hub,
            settings=settings,
            task_plans=task_plans,
        )
        return response or "این فرمان در Bridge پشتیبانی نمی‌شود."

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            pass

    async def advance_task_plans_until_stopped() -> None:
        """Advance only plans explicitly started by the owner in the durable plan store."""
        while not stop.is_set():
            try:
                events = await advance_ready_plan_steps(
                    hub,
                    task_plans,
                    None,
                    settings.owner_ids,
                )
                submitted = sum(event.kind == "submitted" for event in events)
                blocked = sum(event.kind == "blocked" for event in events)
                if events:
                    LOGGER.info(
                        "Bridge dependency-plan tick completed; submitted=%d blocked=%d",
                        submitted,
                        blocked,
                    )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - scheduler failure must not terminate bridge polling
                LOGGER.warning("Bridge dependency-plan tick failed; a later tick will retry")
            try:
                await asyncio.wait_for(stop.wait(), timeout=5.0)
            except TimeoutError:
                continue

    plan_scheduler = asyncio.create_task(advance_task_plans_until_stopped())
    worker = TelegramBridgeWorker(bridge, handle_job)
    try:
        LOGGER.info("Cloudflare Telegram bridge consumer started")
        await worker.run(stop)
    finally:
        stop.set()
        plan_scheduler.cancel()
        await asyncio.gather(plan_scheduler, return_exceptions=True)
        try:
            await hub.close()
        finally:
            LOGGER.info("Cloudflare Telegram bridge consumer stopped")


async def run(settings: Settings) -> None:
    if settings.telegram_transport == "cloudflare_bridge":
        await _run_cloudflare_bridge(settings)
        return
    if settings.telegram_bot_token is None:
        raise SystemExit("TELEGRAM_BOT_TOKEN is required for local polling mode")
    try:
        client_id = load_or_create_client_id(settings.state_dir)
    except InstanceIdentityError as exc:
        raise SystemExit(str(exc)) from exc

    cursor = UpdateCursor(settings.state_dir / "telegram-update-offset.json")
    try:
        offset = cursor.load()
    except UpdateCursorError as exc:
        raise SystemExit("Stored Telegram update cursor is invalid or inaccessible") from exc

    task_watches = TaskWatchStore(settings.state_dir / "task-watches.json")
    try:
        task_watches.load()
    except TaskWatchError as exc:
        raise SystemExit("Stored Telegram task-watch state is invalid or inaccessible") from exc

    task_plans = TaskPlanStore(settings.state_dir / "task-plans.json")
    try:
        task_plans.load()
    except TaskPlanError as exc:
        raise SystemExit("Stored Telegram task-plan state is invalid or inaccessible") from exc

    business_connections = BusinessConnectionStore(
        settings.state_dir / "business-connections.json"
    )
    try:
        business_connections.load()
    except BusinessConnectionError as exc:
        raise SystemExit(
            "Stored Telegram Business connection state is invalid or inaccessible"
        ) from exc
    business_processor = BusinessEventProcessor(
        business_connections,
        settings.owner_ids,
    )

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

    backoff = 1
    metrics = RuntimeMetrics()
    last_task_watch_poll = 0.0

    try:
        await _register_command_menu(api, settings.owner_ids)
    except TelegramAPIError:
        LOGGER.warning("Telegram command menu registration failed; polling continues")

    try:
        LOGGER.info("Telegram polling started; environment=%s", settings.environment)
        while not stop.is_set():
            try:
                updates = await api.get_updates(offset=offset, timeout=settings.poll_timeout_seconds)
                metrics.record_poll(len(updates))
                backoff = 1
                for raw_update in updates:
                    try:
                        parsed_update = parse_telegram_update(raw_update)
                    except TelegramUpdateError:
                        LOGGER.warning("Telegram returned an invalid update envelope")
                        raise TelegramAPIError(
                            "Telegram returned an invalid update envelope"
                        ) from None
                    update_id = parsed_update.update_id
                    if parsed_update.business_event is not None:
                        try:
                            business_result = _process_business_update(
                                parsed_update,
                                business_processor,
                            )
                        except BusinessConnectionError:
                            LOGGER.warning(
                                "Telegram Business state could not be stored; update cursor was not advanced"
                            )
                            raise TelegramAPIError(
                                "Telegram Business state could not be stored"
                            ) from None
                        if business_result.status in {"ignored", "rejected"}:
                            metrics.record_ignored_update()
                        LOGGER.info(
                            "Telegram Business update processed: event=%s outcome=%s",
                            business_result.event_type,
                            business_result.status,
                        )
                        offset = _advance_update_cursor(cursor, offset, update_id)
                        continue

                    message = parsed_update.message
                    if message is None:
                        metrics.record_ignored_update()
                        offset = _advance_update_cursor(cursor, offset, update_id)
                        continue
                    chat = message["chat"]
                    chat_id = chat["id"]
                    if isinstance(chat_id, bool) or not isinstance(chat_id, int):
                        metrics.record_ignored_update()
                        offset = _advance_update_cursor(cursor, offset, update_id)
                        continue
                    response = router.route(message)
                    if response is None:
                        metrics.record_ignored_update()
                        offset = _advance_update_cursor(cursor, offset, update_id)
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
                        if command_name == "/health":
                            response = _runtime_health_response(metrics)
                        elif command_name in OWNER_HUB_COMMANDS:
                            command_response = await _owner_hub_command(
                                text,
                                telegram_user_id=user_id,
                                update_id=update_id if isinstance(update_id, int) else 0,
                                hub=hub,
                                settings=settings,
                                task_watches=task_watches,
                                task_plans=task_plans,
                                api=api,
                                chat_id=chat_id,
                            )
                            if command_response is not None:
                                response = command_response
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
                        metrics.record_reply()
                        offset = _advance_update_cursor(cursor, offset, update_id)
                if (
                    hub is not None
                    and settings.hub_integration_enabled
                    and time.monotonic() - last_task_watch_poll >= 30
                ):
                    await _poll_task_watches(api, hub, task_watches)
                    try:
                        events = await advance_ready_plan_steps(
                            hub, task_plans, task_watches, settings.owner_ids
                        )
                    except Exception:  # noqa: BLE001 - plan advancement must not stop Telegram polling
                        LOGGER.exception("Dependency-aware task-plan advancement failed")
                        events = ()
                    for event in events:
                        if event.kind == "submitted" and event.task_id:
                            message = (
                                f"پیش‌نیازهای مرحله {event.step_index + 1} طرح {event.plan_id} کامل شد؛ "
                                f"وظیفه به Hub ارسال شد.\nشناسه: {event.task_id}\n"
                                f"پیگیری: /taskprogress {event.task_id}"
                            )
                        else:
                            reason = {
                                "dependency_failed": "یکی از پیش‌نیازها ناموفق یا لغوشده است",
                                "dependency_blocked": "یکی از پیش‌نیازهای قبلی مسدود شده است",
                                "hub_policy_denied": "Hub به دلیل مجوز یا سیاست اجرا، ثبت مرحله را رد کرد",
                            }.get(event.reason or "", "پیش‌نیازها کامل نشدند")
                            message = (
                                f"مرحله {event.step_index + 1} طرح {event.plan_id} مسدود شد: {reason}.\n"
                                f"وضعیت کامل: /taskplanstatus {event.plan_id}"
                            )
                        try:
                            await _send_response(api, event.telegram_user_id, message)
                        except TelegramAPIError:
                            LOGGER.warning("Task-plan advancement notification failed or has unknown outcome")
                    last_task_watch_poll = time.monotonic()
                metrics.emit(LOGGER)
            except TelegramAPIError:
                metrics.record_telegram_api_error()
                metrics.emit(LOGGER)
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
        metrics.emit(LOGGER)
        LOGGER.info("Telegram polling stopped")


def main() -> None:
    # Explicit shell/OS environment variables take precedence over local .env values.
    load_dotenv(override=False)
    parser = argparse.ArgumentParser(prog="el-ai")
    parser.add_argument("command", nargs="?", choices=("run", "doctor"), default="run")
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args()
    if args.command == "doctor":
        from el_ai.doctor import main as doctor_main

        raise SystemExit(doctor_main(json_output=args.json_output))
    if args.json_output:
        parser.error("--json is only supported with the doctor command")

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
