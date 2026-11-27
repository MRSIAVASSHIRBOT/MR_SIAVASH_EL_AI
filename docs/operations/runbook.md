# Operations Runbook

## Local development

1. Install Python 3.11 or newer.
2. Create and activate a virtual environment.
3. Install `pip install -e ".[dev]"`.
4. Copy `.env.example` to `.env` and set the bot token locally.
5. Set `TELEGRAM_OWNER_IDS` to the numeric ID of the owner.
6. Keep `TELEGRAM_PUBLIC_ENABLED=false` until public capabilities have been reviewed.
7. Start with `python -m el_ai`.
8. On startup, the bot registers `/start` and `/help` for private chats and a separate command menu for each configured owner; menu registration failure is non-fatal and polling continues.

## Secret handling

Never commit `.env`, bot tokens, Hub credentials or provider keys. Rotate a token immediately if exposed. Do not send tokens through Telegram messages or chat.

## Owner video commands

The owner-only `/videoproviders`, `/videohistory` and `/videostatus` commands use the Hub's paired Telegram API. Grant `video:read` for provider metadata, history, status, and the read-only `/videoroute SECONDS ASPECT [720p|1080p] [remote-ok spend-ok USD_CAP]` preview. The preview evaluates local/hosted route policy and explicit consent caps only; it starts no job, submits no prompt or media, and incurs no provider charge. Grant `video:cancel` separately only when pending-generation cancellation is required. Existing pairings do not gain these scopes automatically; the authenticated Hub owner can explicitly update the active client's scopes with `PUT /integrations/telegram/clients/{client_id}/scopes`. Cancellation is limited to prepared or queued work before provider submission; active or ambiguous provider jobs are never reported as cancelled.
## Shutdown and failure

The polling loop handles SIGINT/SIGTERM and uses bounded exponential backoff for Telegram API failures. When the Bot API returns HTTP 429 (or an explicit JSON error code 429), EL_AI reads only the structured `parameters.retry_after` integer and waits for that duration when it is within the configured safe bound. Missing or invalid hints use the bounded exponential fallback. A confirmed 429 rejection returns only the rejected chunk to the outbox's pending state and leaves the Telegram update cursor unchanged; the next poll can resume the same response while skipping chunks whose delivery was already confirmed. Transport timeouts, connection failures, malformed success responses, and other ambiguous outcomes remain quarantined and are never automatically resent. The outbox stores content digests and delivery state, not response text.

Long responses are split into ordered messages that stay within Telegram's 4096 UTF-16 code-unit limit; the complete response text is preserved.

## Production

Production deployment is not included in the bootstrap. Before webhook deployment, define TLS termination, exact webhook secret validation, request size/rate limits, replay/update deduplication, monitoring, log retention and rollback. Never expose the local Hub API or create a public tunnel to a development machine.


## Task status notifications

- Owner-only `/taskwatch TASK_ID` persists a watch for an active Hub task and sends a Telegram message when its status changes.
- Use `/taskwatches` to list the current owner's watches and `/taskunwatch TASK_ID` to stop one.
- Watches are stored locally in `task-watches.json` with mode 0600 where supported; only task IDs, Telegram owner IDs and last-known statuses are stored.
- Notifications are polled while the bot process is running. A restart resumes persisted watches; terminal tasks are removed after a successful notification. Delivery is best-effort and an ambiguous Telegram send can be repeated.
