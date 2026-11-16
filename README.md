# MR_SIAVASH_EL_AI

Advanced Telegram interface for the MR_SIAVASH ecosystem.

**Product scope:** hybrid public + owner experience, with private memory, task planning and policy-controlled execution delegated to MR_SIAVASH_HUB_AI.

## Trust boundaries

- EL_AI owns Telegram transport, Telegram identity mapping, command routing and user-facing delivery.
- MR_SIAVASH_HUB_AI remains authoritative for private memory, tasks, agents, approvals, tool policy and audit.
- MR_SIAVASH_AI is a public website and public-knowledge Gateway only.
- No shared database, browser-held Hub credential, public tunnel, or implicit trust between projects.
- Private Hub access uses owner-initiated, Telegram-ID-bound pairing and dedicated scoped API routes.
- Hub credentials are stored in an OS-backed credential manager; no owner password or unrestricted Hub session is stored by EL_AI.
- Public website queries send only the current message to the allowlisted public Gateway; private Hub context is never forwarded.

## Current implementation status

The Telegram transport, owner/public separation, scoped Hub client, stable local client identity, OS-backed Keyring storage, and public MR_SIAVASH_AI assistant client are implemented on `main`. Public conversations and Hub access remain opt-in. No bot token has been configured, no live Telegram bot has been connected, and no production bot deployment has occurred.

The production website Gateway currently has active Workers AI and 90-day support-retention configuration. Its current 100% traffic deployment is Worker version `62e8a337-d804-4748-8bb2-d5e0180ee2f8` (version 14), deployed on 2026-11-11 12:31:27 UTC from MR_SIAVASH_AI commit `1616545a2e204cb378e2c824299412ca20ef598b`. The production D1 binding remains `mr-siavash-ai-prod`, the Workers AI binding is present, and query-string redaction is enabled. The daily `0 3 * * *` UTC cleanup schedule remains configured. Cloudflare Workers Observability recorded `support_retention_cleanup_completed` events on 2026-11-12 03:00:24.092 UTC and 2026-11-13 03:00:23.971 UTC. The 13 November event reported a 90-day retention period and aggregate deletion counts of 0 support tickets, 0 idempotency records, and 0 rate-limit buckets. These are invocation-level aggregate telemetry, not proof of every possible retention state. The current website source is 40 commits ahead of the deployed source annotation; see the [website release evidence](https://github.com/MRSIAVASSHIRBOT/MR_SIAVASH_AI/blob/main/docs/RELEASE_STATUS.md) and [source-drift issue #120](https://github.com/MRSIAVASSHIRBOT/MR_SIAVASH_AI/issues/120). No newer Worker deployment is inferred. This describes the website Gateway only; it does not mean the Telegram bot is connected or deployed.

See docs/architecture/overview.md, docs/security/threat-model.md, and docs/roadmap/implementation-plan.md.

## Development

Python 3.11+.

```bash
python -m venv .venv
# activate the virtual environment, then:
python -m pip install -e ".[dev]"
cp .env.example .env
python -m el_ai
```

The bot uses long polling for local development. Production webhook deployment is a separate release milestone and must use a secret-validated endpoint and TLS.

Run the read-only local preflight before configuring or starting the bot:

```bash
el-ai doctor
el-ai doctor --json
# or:
python -m el_ai doctor
```

The preflight does not contact Telegram, Hub, or the public Gateway, does not write files, and never prints credentials. It checks required environment settings, local state-directory access, Hub URL policy and trusted keyring availability (only when Hub integration is enabled). It does not prove that a Telegram token is valid or that remote services are reachable. See [docs/operations/preflight.md](docs/operations/preflight.md).

## Configuration

- TELEGRAM_BOT_TOKEN: required runtime secret.
- TELEGRAM_OWNER_IDS: comma-separated numeric Telegram user IDs allowed to use owner commands.
- TELEGRAM_PUBLIC_ENABLED: whether public conversations are enabled (default false).
- EL_AI_ENVIRONMENT: development, test or production.
- EL_AI_STATE_DIR: local state directory containing a non-secret, stable client UUID.
- HUB_API_BASE_URL: local Hub URL (example: http://127.0.0.1:8000); remote URLs are rejected by default.
- HUB_INTEGRATION_ENABLED: opt-in for paired Hub commands; defaults to false.
- HUB_ALLOW_REMOTE: defaults to false; enabling remote Hub URLs requires a separate hosting/security decision and HTTPS.
- SITE_PUBLIC_API_BASE_URL: public-only MR_SIAVASH_AI Gateway URL.
- SITE_PUBLIC_WEB_BASE_URL: base URL used to validate and render public source links.

## Owner Telegram commands

- `/pairinfo`: show the numeric Telegram ID and stable EL_AI client UUID needed to create a Hub pairing challenge.
- `/pair CODE`: exchange a five-minute, one-time Hub pairing code and store credentials in the OS credential vault.
- `/owner`: confirm that the sender is the configured owner.
- `/hub`: check the paired Hub connection.
- `/tasks [STATUS] [CURSOR]`: list this paired bot's tasks with bounded cursor-based pagination.
- `/task TYPE | DESCRIPTION`: submit a task through Hub policy and idempotency controls.
- `/taskstatus TASK_ID`, `/taskhistory TASK_ID`, and `/taskresult TASK_ID`: inspect the status, event history, or final result of a task submitted by this paired client.
- `/taskcancel TASK_ID`: cancel a task only while it is queued or waiting for approval.
- `/taskwatch TASK_ID`, `/taskwatches`, and `/taskunwatch TASK_ID`: persist, list, or stop owner-only task status notifications. Watches store task IDs, Telegram owner IDs, and last-known status only; delivery is best-effort.
- `/memory QUERY`: search the owner's Hub memory.
- `/videoproviders`: inspect the non-secret configured video provider catalog.
- `/videoroute SECONDS ASPECT [720p|1080p] [remote-ok spend-ok USD_CAP]`: request a read-only local-first route preview. It starts no generation, submits no prompt or media, and creates no provider charge.
- `/videohistory [CURSOR]` and `/videostatus GENERATION_ID`: inspect bounded, owner-scoped video generation history and status.
- `/videocancel GENERATION_ID`: cancel only a prepared or queued generation before provider submission; active or ambiguous provider work is not reported as cancelled.
- `/health`: inspect aggregate polling health and counters (owner-only; no message or user data is exposed).
- `/site QUERY`: query public website knowledge without sending private Hub context.

Video catalog, route preview, history, and status require the paired client's `video:read` scope. Pending-generation cancellation separately requires `video:cancel`. Existing pairings do not receive new scopes automatically; the authenticated Hub owner must explicitly update the client scope set.

When TELEGRAM_PUBLIC_ENABLED=true, public users can send normal text in a private chat and receive answers from the public website Gateway. Group chats remain blocked.

Pairing is owner-initiated from the local Hub. The bot must run in a private Telegram chat. Never send the Hub owner password or access/refresh tokens to Telegram.

## Safety

Public users cannot access owner commands or private state. All privileged operations must be explicitly allowlisted by capability and authorized by Hub policy. Sensitive or consequential actions require Hub approval. See the security documentation before enabling integrations.
