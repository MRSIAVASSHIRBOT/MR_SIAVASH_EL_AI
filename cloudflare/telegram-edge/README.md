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
