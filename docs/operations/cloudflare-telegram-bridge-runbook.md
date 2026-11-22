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

## Zero-cost local Hub operating profile

For a USD 0/month setup, run the Hub API, Hub pull worker, and EL_AI bridge consumer on the **same owner-controlled Windows PC**. Keep the Hub API bound to loopback. The Cloudflare Worker is only an optional Telegram edge inbox; it does not call into the private PC and the PC does not need a public inbound port or tunnel.

Start the Hub first from its repository root:

```powershell
.\scripts\start-local.ps1
```

Confirm that `http://127.0.0.1:8000/ready` reports the API and worker ready. Then configure EL_AI's ignored local `.env` file with the following shape (replace every placeholder locally; never commit real values):

```dotenv
TELEGRAM_TRANSPORT=cloudflare_bridge
TELEGRAM_BRIDGE_URL=https://mr-siavash-el-ir-bot.mrsiavashirbot.workers.dev
TELEGRAM_BRIDGE_SHARED_SECRET=<same independently generated secret on the Worker and local consumer>
TELEGRAM_OWNER_IDS=<numeric owner Telegram ID>
HUB_INTEGRATION_ENABLED=true
HUB_API_BASE_URL=http://127.0.0.1:8000
HUB_ALLOW_REMOTE=false
HUB_ALLOW_RENDER_PRIVATE_NETWORK=false
EL_AI_CREDENTIAL_STORE=keyring
EL_AI_STATE_DIR=./data
TELEGRAM_PUBLIC_ENABLED=false
```

The bridge transport does not need `TELEGRAM_BOT_TOKEN` in the local Python process; Telegram ingress and reply delivery are performed by the Worker. Keep `TELEGRAM_BRIDGE_SHARED_SECRET` in the local secret environment and the Worker's secret binding only. The owner ID must match the Worker's `ALLOWED_TELEGRAM_USER_ID`. On Windows, retain the OS-backed Keyring credential store; do not switch to plaintext or the POSIX encrypted-file backend.

Run `el-ai doctor` and then `python -m el_ai` from the EL_AI repository root. The consumer makes outbound HTTPS requests to the Worker, pulls one signed job at a time, and calls the Hub through loopback. It must not expose port 8000 to the public internet. Do not run local polling at the same time as an active Telegram webhook.

When the PC is asleep, powered off, or disconnected, Hub processing and local inference stop. Edge ingress may accept and queue updates while available, but this is not a 24/7 processing guarantee or a substitute for a tested backup. After restart, verify Hub `/ready`, run `el-ai doctor`, start the consumer, and test one harmless owner command before relying on the integration.

**Production compatibility gate:** the live Worker settings last inspected on 2026-11-22 did not contain `TELEGRAM_WEBHOOK_URL`. The versioned Worker source in EL_AI PR #106 requires that explicit non-secret setting. Do not deploy that source to the production Worker until the correct current production webhook URL is configured and read back. No production Worker, webhook, D1, or secret change is part of this local profile.

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
