# Cloudflare Telegram Edge Bridge — observed state

**Verification date:** 2026-11-20  
**Repository main inspected:** `edc1b6bc9048fa0843e0623d8177efcd88c8f33f`  
**Cloudflare account state:** read-only inspection

## Existing Worker

The Cloudflare account already contains a Worker named `mr-siavash-el-ir-bot`. The observed deployment list shows deployment `4b27150f-3aa8-42a5-a88f-72933887d3a8`, created 2026-11-11 16:41:09 UTC, with version `ce36c4e3-25b4-4a3d-b6fb-53f572bdf98c` receiving 100% traffic. A preceding deployment is annotated as `Deploy cloud-first Telegram edge with protected...` and was also 100% traffic. This verifies that a Worker was deployed, **not** that Telegram webhook registration succeeded, required secrets are configured, or end-to-end messages are working.

## Observed edge implementation

The deployed Worker script includes:

- `GET /health` and `GET /ready` checks.
- `POST /admin/register-webhook` for guarded webhook setup.
- `POST /telegram/webhook` for Telegram update ingress.
- `POST /bridge/pull` and `POST /bridge/complete` for a signed bridge protocol.
- Bounded request body parsing (64 KiB maximum).
- Telegram webhook secret validation and constant-time comparison helper.
- Update deduplication/claim leases using D1 table `telegram_processed_updates`.
- Queue table `telegram_jobs`, encrypted queued payloads using AES-GCM, and lease-token fencing for bridge claims/completion.
- A bounded Telegram API timeout and safe error handling.

Environment references in the script include Telegram token/webhook secrets, numeric owner allowlist, D1 binding, bridge shared secret, queue-encryption key and optional Hub client identifier. Names are listed here only as configuration categories; secret values must never be placed in repository files or chat.

## Intended trust boundaries

Telegram -> Cloudflare Worker webhook/queue -> authenticated bridge adapter -> EL_AI command/application logic -> scoped MR_SIAVASH_HUB_AI API.

MR_SIAVASH_AI's public Gateway is not a substitute for the private Hub API. It must not receive private memory, task data, Hub tokens or owner context.

## Integration gap / next engineering slice

The Python repository's local long-polling runtime remains the default. The feature branch `feat/cloudflare-telegram-bridge-client` now adds a standalone signed Python bridge client for the deployed `/bridge/pull` and `/bridge/complete` contract, including HMAC request signing, response validation, lease-token handling, and sanitized HTTP failures. It is not yet wired into the application runtime; no worker loop, Hub command dispatch, hosted process, or end-to-end message delivery is claimed.

The next safe implementation slice is to add a bridge client/worker interface to the Python application that:

1. Validates the signed bridge protocol and bounded payloads.
2. Uses existing Telegram command routing and authorization behavior rather than duplicating business logic at the edge.
3. Claims work using the edge lease contract; completes only with the matching lease token.
4. Preserves update idempotency and safe failure behavior.
5. Rejects unknown commands, non-private chats, and non-owner commands according to existing EL_AI policy.
6. Uses only an HTTPS, explicitly allowlisted, owner-scoped Hub endpoint; never a localhost URL from Cloudflare.
7. Adds tests for invalid signatures, malformed/oversized payloads, duplicate updates, expired/replaced leases, transient failure and safe completion.

## Release acceptance gates

- Confirm the Worker’s D1 bindings/schema and deployed source provenance.
- Confirm secret presence without revealing secret values.
- Validate webhook registration by read-back and verify Telegram delivery using owner-controlled credentials.
- Implement and test the hosted bridge adapter and private Hub reachability.
- Confirm production privacy settings, bounded retries, observability and rollback.
- Complete owner/admin review before production secret/configuration changes or deployment.

No Worker, Telegram webhook, D1 schema, secrets, route, domain or production setting was changed during this inspection.


## Live read-only checkpoint — 2026-11-22 UTC

The Cloudflare account and deployed Worker were re-read without changing configuration or requesting secret values.

### Deployment and binding metadata

- Worker: `mr-siavash-el-ir-bot`.
- Latest listed deployment: `4b27150f-3aa8-42a5-a88f-72933887d3a8`, created 2026-11-11 16:41:09 UTC.
- The deployment assigns 100% traffic to version `ce36c4e3-25b4-4a3d-b6fb-53f572bdf98c` (version number 17).
- The Worker subdomain is `mrsiavashirbot`; the Worker has its `workers.dev` subdomain enabled and preview URLs disabled. The account-level Worker Domains query returned no custom Worker domains.
- The secret-binding inventory contains these names, all with type `secret_text`: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET`, `WEBHOOK_SETUP_SECRET`, `BRIDGE_SHARED_SECRET`, and `QUEUE_ENCRYPTION_KEY`. **No secret values were read, printed, or changed.**

### Live endpoint checks

- `GET https://mr-siavash-el-ir-bot.mrsiavashirbot.workers.dev/health` returned HTTP-success content: `{"status":"ok","service":"mr-siavash-el-ir-bot-edge"}`.
- `GET https://mr-siavash-el-ir-bot.mrsiavashirbot.workers.dev/ready` returned HTTP-success content: `{"status":"ready","service":"mr-siavash-el-ir-bot-edge"}`.

The deployed source shows that `/ready` checks the presence of the Telegram token and webhook secret, a D1 binding with a `prepare` method, a numeric owner-ID environment value, and the webhook-secret format. It does **not** call Telegram to validate the token or read back webhook registration. The bridge routes separately validate the bridge shared-secret length and queue-encryption-key format; those checks are not part of the `/ready` predicate.

### What remains unverified

- Telegram token validity and `getWebhookInfo` / webhook registration state.
- Delivery of a real owner Telegram message through the webhook.
- D1 schema and table availability; no SQL or rows were queried.
- A signed `/bridge/pull` and `/bridge/complete` round-trip.
- EL_AI Python bridge-client connectivity and private Hub integration.
- End-to-end owner command routing and response delivery.

No webhook setup call, Telegram API request, D1 query, bridge request, secret mutation, Worker deployment, route change, or production configuration change was performed. The edge Worker is live and ready according to its own health predicate, but the Telegram bot is **not** yet accepted as end-to-end operational.



## Python runtime integration — merged to main

The merged mainline implementation adds an explicit runtime selector:

- `TELEGRAM_TRANSPORT=local_polling` (default) keeps the existing Telegram Bot API long-polling runtime.
- `TELEGRAM_TRANSPORT=cloudflare_bridge` starts only the signed Cloudflare bridge consumer. It does not instantiate the local Telegram Bot API client or call `getUpdates`, preventing two competing update consumers.
- Bridge mode requires `TELEGRAM_BRIDGE_URL`, `TELEGRAM_BRIDGE_SHARED_SECRET` (at least 32 characters), a non-empty `TELEGRAM_OWNER_IDS` allowlist, and `HUB_INTEGRATION_ENABLED=true` with the normal Hub URL policy. The bot token is not required by the Python bridge consumer because Telegram ingress and reply delivery remain on the Worker.
- The bridge handler accepts `{command: "pair", pairing_code: ...}` and bounded `{command: "dispatch", text: ...}` payloads for a fixed owner Hub command allowlist. Python independently validates the private owner identity, exact payload keys, bounded single-line text, and command allowlist before reusing `_owner_hub_command`. Background-notification commands and binary `/videorenderdownload` remain excluded until periodic notifications and file upload are implemented for bridge mode.
- The local Python process still needs a trusted OS keyring for Hub credential storage. If that keyring is unavailable, bridge mode exits rather than falling back to insecure credential storage.

Example environment (values are placeholders; never commit secrets):

```dotenv
TELEGRAM_TRANSPORT=cloudflare_bridge
TELEGRAM_BRIDGE_URL=https://mr-siavash-el-ir-bot.mrsiavashirbot.workers.dev
TELEGRAM_BRIDGE_SHARED_SECRET=<the same secret configured on the Worker>
TELEGRAM_OWNER_IDS=<numeric owner Telegram ID>
HUB_INTEGRATION_ENABLED=true
HUB_API_BASE_URL=https://<your-private-Hub-endpoint>
HUB_ALLOW_REMOTE=true
```

This is runtime code, not a hosted deployment. The Python consumer must run on a trusted machine with access to the Hub and its OS keyring. The runtime code was merged to main, but no Cloudflare Worker, webhook, secret, or production configuration was changed. End-to-end operation remains unverified until a consumer is started with the actual environment and a real owner command is round-tripped.


## Production observability checkpoint — 2026-11-22 UTC

The Cloudflare Worker settings were inspected and the query-string redaction flag was enabled. A subsequent settings read-back verified:

- Query-string redaction: enabled.
- Observability: enabled.
- Invocation logs: enabled, persisted, with sampling configured at 1.0.
- Traces: enabled and persisted, with head sampling configured at 0.1.
- The latest deployment metadata after the settings update showed 100% traffic assigned to deployment `c22cef09-77f8-494f-a668-e22d51bd497c`, version `f8ec5020-6041-4436-bfa4-f5a8ad89d1b6`.

This is a settings/deployment-metadata observation only. It does not constitute a verified live HTTP health check, Telegram webhook confirmation, D1 query, or end-to-end bridge test. The earlier 2026-11-20 checkpoint above describes the earlier observed state; this section supersedes its observability and deployment snapshot where values differ.

## Versioned Worker source and D1 schema — 2026-11-22

A read-only Cloudflare source export was used to seed the version-controlled Worker implementation under `cloudflare/telegram-edge/src/index.js`. The source corresponds to the live deployment snapshot recorded above in that directory's README. The tracked version adds only fixed-allowlist network cause metadata to webhook registration failures; arbitrary exception messages and cause strings are not logged.

The application-owned D1 schema was also read without writes and recorded in `cloudflare/telegram-edge/migrations/0001_initial.sql`. It contains the four application tables and four indexes; Cloudflare's internal `_cf_KV` table is excluded. No production database identifier, secret value, or account identifier was copied into Git.

A Node built-in test suite and a dedicated GitHub Actions workflow cover webhook-registration network diagnostics and Telegram API read-back. The source and tests are being introduced on a feature branch; **this is not a production deployment**. The live Worker, webhook registration, secrets, D1 data and settings have not been changed by this source export.

## Environment-specific webhook URL contract — 2026-11-23

The versioned Worker now requires the non-secret `TELEGRAM_WEBHOOK_URL` environment variable. It is validated as an HTTPS URL with the exact `/telegram/webhook` path, a qualified hostname, no embedded credentials, and no query string or fragment. `/ready` returns not-ready when this variable is missing or invalid. Manual registration and scheduled webhook read-back use the same resolved environment URL; there is no production URL constant in the Worker source.

The Cloudflare live Worker settings read on 2026-11-22 did not contain a `TELEGRAM_WEBHOOK_URL` binding. The currently deployed version still uses its existing source and remains unchanged. Before a future production release of this branch, add the plain-text variable to the production Worker configuration with its current production endpoint (`https://mr-siavash-el-ir-bot.mrsiavashirbot.workers.dev/telegram/webhook`) and verify the configuration read-back. Do not put this URL in a secret binding; do not deploy before this compatibility step.

The development sample sets a separate `mr-siavash-el-ai-edge-dev` URL, uses its own D1 database name, and intentionally omits Cron Triggers. No development Worker or D1 database has been created by this source/configuration change.
