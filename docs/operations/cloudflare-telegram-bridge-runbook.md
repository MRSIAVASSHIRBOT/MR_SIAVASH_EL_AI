# Cloudflare Telegram Bridge Operations Runbook

This runbook covers the Cloudflare webhook ingress and the Python EL_AI bridge consumer. It intentionally keeps all credential values out of Git, chat, command output, and logs.

## Architecture and operating modes

- **Local polling**: the Python runtime calls Telegram `getUpdates`. Use this for local development and only when the webhook is not active.
- **Cloudflare bridge**: Telegram sends webhook updates to the edge Worker. The Python consumer pulls signed, encrypted jobs from Cloudflare and sends completion acknowledgements. In bridge mode, EL_AI does not start `getUpdates`.
- Run exactly one Telegram update transport at a time. Do not run local polling while webhook ingress is active.
- The Worker is not the complete bot runtime. The bridge consumer needs a trusted machine with EL_AI installed, Hub connectivity, owner allowlist configuration, and protected credential storage.

## Preflight (read-only)

Run:

```sh
el-ai doctor
el-ai doctor --json
```

The preflight is offline and read-only. A passing result checks local configuration shape; it does not validate the token, webhook, Hub connectivity, or message delivery.

## Cloudflare edge checks

Confirm the deployed Worker responds:

```sh
curl -fsS --connect-timeout 10 https://mr-siavash-el-ir-bot.mrsiavashirbot.workers.dev/health
curl -fsS --connect-timeout 10 https://mr-siavash-el-ir-bot.mrsiavashirbot.workers.dev/ready
```

- `/health` checks that the Worker is serving.
- `/ready` checks configured bindings and basic configuration shape. It does **not** call Telegram or prove webhook registration.
- Never treat HTTP success on these endpoints as end-to-end acceptance.

## Secret inventory (names only)

The Worker uses these secret bindings. Secret values must never be printed or committed:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_WEBHOOK_SECRET`
- `WEBHOOK_SETUP_SECRET`
- `BRIDGE_SHARED_SECRET`
- `QUEUE_ENCRYPTION_KEY`

If a BotFather token was exposed, revoke it and replace the Cloudflare binding. Secret inventory/readback must only report binding names and types, never values.

## Webhook registration and read-back

The guarded `POST /admin/register-webhook` route is the setup entry point. It requires the dedicated setup authorization secret and must be invoked only from an authorized environment. Do not put that secret in repository files, Telegram messages, screenshots, shell history, or this runbook.

Registration is accepted only when Telegram's `setWebhook` response succeeds and a subsequent `getWebhookInfo` read-back confirms the configured URL. The Worker reports sanitized diagnostic categories (stage/kind/status) on failure; logs must not contain the Bot Token, setup secret, webhook secret, bridge secret, raw request body, or user message.

## Bridge consumer configuration

The Python runtime requires the following settings in its trusted local environment for bridge mode:

- `TELEGRAM_TRANSPORT=cloudflare_bridge`
- `TELEGRAM_BRIDGE_URL` set to the Worker HTTPS base URL
- `TELEGRAM_BRIDGE_SHARED_SECRET` (minimum 32 characters)
- `TELEGRAM_OWNER_IDS` containing the numeric owner ID
- `HUB_INTEGRATION_ENABLED=true`
- `HUB_API_BASE_URL` reachable by the local consumer
- `EL_AI_CREDENTIAL_STORE=encrypted_file` with `EL_AI_CREDENTIAL_ENCRYPTION_KEY` set to a base64-encoded 32-byte host secret on POSIX hosts; local Windows installations should retain the OS keyring

The Worker currently uses the plain-text binding `ALLOWED_TELEGRAM_USER_ID` (singular). Its numeric value must match the single owner ID configured in the Python runtime's `TELEGRAM_OWNER_IDS`. Verify the names and compare values only inside the authorized secret/configuration interfaces; do not copy the ID into Git, logs, screenshots, or chat. Public chat is disabled by default; this bridge runbook does not enable it.

Start the consumer only after the local preflight passes and the configured credential backend is confirmed. The encrypted-file backend is POSIX-only and uses AES-256-GCM; Windows hosts must use the OS keyring. No plaintext fallback is supported. Stop it with a normal SIGINT/SIGTERM so the worker cancels active handlers without acknowledging their lease; the lease can then expire and allow redelivery.

## Acceptance checklist

Mark each gate independently:

- [ ] Worker `/health` succeeds.
- [ ] Worker `/ready` succeeds.
- [ ] Secret-binding **names** are present (values never retrieved).
- [ ] Telegram webhook is registered and `getWebhookInfo` confirms the exact Worker URL.
- [ ] A private owner test message reaches the edge queue and is processed by the local consumer.
- [ ] The reply is received in Telegram.
- [ ] Owner pairing with Hub is tested separately; webhook success alone does not establish pairing.
- [ ] Retry, shutdown, and redelivery behavior is reviewed before production use.

## Troubleshooting boundaries

- **Telegram API timeout**: a client-side timeout is not proof of token rejection. Check whether the request received an HTTP/API response before diagnosing credentials.
- **Worker healthy but webhook absent**: health/readiness do not register a webhook; use the guarded registration route and read-back.
- **Webhook succeeds but no bot reply**: check the local consumer process, bridge pull/completion telemetry, Hub opt-in, protected credential storage, and owner allowlist. Do not enable public mode as a workaround.
- **No logs available**: use supported Worker Tail/telemetry surfaces, filtering for sanitized error events. Never dump complete request headers or payloads.
- **Rollback**: use the last known-good Worker deployment/version after reviewing traffic assignments. Do not delete the Worker, D1 database, queues, or secrets as a diagnostic shortcut.

## Release status

A Worker deployment and a passing `/ready` check are infrastructure evidence only. Production readiness requires verified webhook read-back and an end-to-end private owner message/reply test. Keep the release status **pending** until those gates have evidence.
