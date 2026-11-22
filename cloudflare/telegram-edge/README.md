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

The tests use Node's built-in test runner and mock only the outbound Telegram HTTP boundary. They cover:

- bounded network failure diagnostics for `setWebhook`;
- rejection of unknown error codes to prevent arbitrary diagnostic data from entering logs; and
- successful `setWebhook` plus `getWebhookInfo` read-back.

Run from this directory:

```bash
npm test
```

No third-party Node dependencies are required.

## Deployment boundary

**This directory is not yet a production deployment configuration.** No production account ID, D1 database ID, token, secret, or Wrangler deployment target is committed. The source retains the observed non-secret production `workers.dev` webhook URL as a default for parity with the deployed Worker; it must be overridden or replaced in a separately designed development configuration. There is intentionally no production `wrangler.jsonc` yet: local/dev deployment must first have a separate D1 database, a separate test Telegram bot, dedicated development secrets, and a non-production webhook URL.

Do not deploy this snapshot directly to the live Worker. The currently deployed Worker remains unchanged until a separate reviewed release updates the source, bindings, migrations, and deployment target together.

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
