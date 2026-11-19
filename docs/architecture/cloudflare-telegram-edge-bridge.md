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

The Python repository currently documents local long-polling as its implemented runtime. Its current main branch does not document or verify a Cloudflare bridge adapter. The deployed edge script has a pull/complete bridge contract, but the complete bridge runtime, durable hosted execution, and private Hub connectivity must be verified before declaring end-to-end readiness.

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
