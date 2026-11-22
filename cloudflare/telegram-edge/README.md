# Cloudflare Telegram Edge Worker

This directory versions the Cloudflare Worker that receives Telegram webhook updates and exposes the signed bridge pull/complete protocol used by the Python EL_AI consumer.

## Source provenance

The initial source snapshot was retrieved read-only from the live Worker `mr-siavash-el-ir-bot` on **2026-11-22**. The latest observed deployment was:

- Deployment: `c22cef09-77f8-494f-a668-e22d51bd497c`
- Worker version: `f8ec5020-6041-4436-bfa4-f5a8ad89d1b6`
- Traffic assigned to that version at inspection: 100%
- Compatibility date: `2026-11-11`

The tracked `src/index.js` is based on that deployed source. This branch adds bounded, allowlisted network error metadata to the existing webhook-registration diagnostics. It does not log exception messages, request headers, Telegram updates, credential values, owner identifiers, or raw network causes.

The diagnostic metadata is deliberately limited to:
- the existing operation stage and coarse failure kind;
- a known-safe cause name; and
- a cause code from a fixed allowlist (for example `EAI_AGAIN` or `ECONNRESET`).

Unknown cause names/codes are omitted. This makes DNS/TLS/connection investigations more actionable when the runtime exposes a recognized code, without recording arbitrary exception text.

## Live configuration snapshot

Only binding names and types were read. Secret values and database identifiers are intentionally not stored here.

| Binding | Cloudflare binding type |
| --- | --- |
| `ALLOWED_TELEGRAM_USER_ID` | `plain_text` |
| `BRIDGE_SHARED_SECRET` | `secret_text` |
| `DB` | `d1` |
| `QUEUE_ENCRYPTION_KEY` | `secret_text` |
| `TELEGRAM_BOT_TOKEN` | `secret_text` |
| `TELEGRAM_WEBHOOK_SECRET` | `secret_text` |
| `WEBHOOK_SETUP_SECRET` | `secret_text` |

Workers Logs and Traces were enabled at inspection. The D1 snapshot contains four application-owned tables (`bridge_nonces`, `telegram_jobs`, `telegram_processed_updates`, and `telegram_sessions`) and four named indexes. The Cloudflare-internal `_cf_KV` table is deliberately excluded from `migrations/0001_initial.sql`.

## Tests

The tests use Node's built-in test runner and require no third-party Node packages. The suites cover:

- bounded webhook-registration network diagnostics and rejection of unknown error codes;
- successful `setWebhook` plus `getWebhookInfo` read-back;
- fail-closed readiness for missing or invalid `TELEGRAM_WEBHOOK_URL` values; and
- a Worker-level bridge round-trip using an in-memory D1 test double: private owner webhook ingress and non-owner filtering, encrypted queue insertion, duplicate-update idempotency, signed HMAC pull and nonce replay rejection, invalid/expired lease fencing with recovery, re-queue and fresh-lease retry after a transient Telegram delivery failure, Telegram reply delivery through a mocked HTTP boundary, duplicate-completion prevention, and empty-queue read-back after completion;
- command-dispatch regression proving allowlisted Hub commands enter the encrypted bridge queue while background-notification commands remain outside the bridge.

A shared deterministic HMAC-SHA256 vector at `test/fixtures/bridge-hmac-v1.json` is consumed by both the Worker E2E test and the Python `TelegramBridgeClient` unit tests. This catches cross-language drift in newline separators, body serialization, hashing, and signature generation.

The Python suite also applies `migrations/0001_initial.sql` to an in-memory SQLite database and checks the application tables, named indexes, unique keys, state constraints, and exclusion of Cloudflare's internal `_cf_KV` table.

These checks exercise Worker routes, the Python request signer, and SQLite-compatible migration syntax. They do not start the Python EL_AI consumer against a deployed Worker, nor contact Telegram/Cloudflare; real Telegram-to-Hub acceptance remains a separate release gate.

Run from this directory:

```bash
npm test
```

No third-party Node dependencies are required.

## Owner command dispatch

The Worker forwards a fixed allowlist of owner Hub commands as encrypted `{ command: "dispatch", text: ... }` jobs after checking the private-chat owner identity, bridge configuration and bounded single-line text. The Python consumer applies a second independent owner check, exact payload-key validation, text bounds and its own command allowlist before invoking the existing Hub handler. `/pair` remains a distinct one-time-code payload.

Bridge mode loads the private local `task-plans.json` store and runs a bounded five-second dependency scheduler. It advances only plans explicitly started by the owner with `/taskplanrun`, submits dependent steps only after every prerequisite succeeds, and reuses stable Hub idempotency keys. `/taskplanstatus` is available for manual inspection. No `TaskWatchStore` is started in bridge mode, so this scheduler does not send Telegram progress notifications. Background-notification commands (`/taskwatch`, `/taskunwatch`, `/taskwatches`, `/taskplanwatch`) and binary `/videorenderdownload` remain excluded until a separate notification loop and Telegram file-upload path are implemented.

## Deployment boundary

**This directory is not yet a production deployment configuration.** No production account ID, D1 database ID, token, secret, or Wrangler deployment target is committed. The Worker no longer hard-codes a production webhook URL: every environment must provide the non-secret `TELEGRAM_WEBHOOK_URL` variable. The runtime accepts only an HTTPS URL with the exact `/telegram/webhook` path and no credentials, query string, or fragment; readiness fails closed when the value is absent or invalid.

The live Worker settings inspected on 2026-11-22 did not include `TELEGRAM_WEBHOOK_URL`. **Do not deploy this source to the production Worker until that plain-text variable is added to the live configuration and read back with the current production webhook URL.** The development sample uses a different Worker URL and a different D1 name. There is intentionally no production `wrangler.jsonc` in this branch.

Do not deploy this snapshot directly to the live Worker. The live Worker, webhook, bindings, secrets, D1 data and deployment settings remain unchanged; production rollout requires a separately reviewed configuration change and release.

## Non-production development setup

Use `wrangler.dev.jsonc.example` as the starting point for an isolated Worker. It uses the Worker name `mr-siavash-el-ai-edge-dev`, a separate `workers.dev` endpoint, a non-production D1 database name, no production route, and **no Cron trigger**. The production Worker's scheduled cleanup/webhook-registration handler therefore does not run in this development configuration.

The sample contains two intentional placeholders:
- `ALLOWED_TELEGRAM_USER_ID`: replace with the numeric ID of the test account that is allowed to use the test bot.
- `d1_databases[0].database_id`: the all-zero UUID is a local-only placeholder, not a real Cloudflare database ID.

Copy the sample before editing; the local copy is ignored by Git:

```bash
cp wrangler.dev.jsonc.example wrangler.dev.jsonc
```

### Local-only D1 and Worker test

Wrangler's default local development mode uses local storage. Apply the migration locally and start the Worker without deploying it:

```bash
npx wrangler d1 migrations apply mr-siavash-el-ai-edge-dev --local --config wrangler.dev.jsonc
npx wrangler dev --config wrangler.dev.jsonc
```

The local Worker is not a public Telegram webhook target. Use the existing Node unit tests for offline verification.

### Separate remote development resources

A remote development test requires a **new D1 database** and a **separate test Telegram bot**. Never reuse the production Worker, D1 database, bot token, webhook secret, bridge secret, or queue-encryption key.

1. Create a D1 database named `mr-siavash-el-ai-edge-dev` in the intended Cloudflare account. Copy only the newly created development database ID into the local `wrangler.dev.jsonc`.
2. Create a separate test bot and configure the development owner's numeric Telegram ID in the local configuration.
3. Set these five values as Wrangler **secrets**, not `vars`: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET`, `WEBHOOK_SETUP_SECRET`, `BRIDGE_SHARED_SECRET`, and `QUEUE_ENCRYPTION_KEY`. Generate independent development-only secrets with the lengths/formats required by the runtime. Do not paste them into chat or commit them.
4. Apply the migration only to the named development database, then deploy only with the explicit development config:

```bash
npx wrangler d1 migrations apply mr-siavash-el-ai-edge-dev --remote --config wrangler.dev.jsonc
npx wrangler deploy --config wrangler.dev.jsonc
```

5. Confirm the deployed URL matches `TELEGRAM_WEBHOOK_URL` in the development configuration before registering the test bot webhook. Do not use the production setup endpoint or its setup secret.

These remote commands create/update Cloudflare resources and can have account-level cost or external effects. They are documentation only and have **not** been executed as part of this PR. The all-zero placeholder database ID must never be used with `--remote`.

### Configuration references

- [Wrangler JSONC configuration](https://developers.cloudflare.com/workers/wrangler/configuration/)
- [Cloudflare D1 migrations](https://developers.cloudflare.com/d1/reference/migrations/)
- [Worker environment variables](https://developers.cloudflare.com/workers/configuration/environment-variables/)
