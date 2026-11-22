# MR_SIAVASH_EL_AI

Advanced Telegram interface for the MR_SIAVASH ecosystem.

**Operational capabilities:** [AWI, GPI, CGI, PAIOS, gap detection, planning, self-evaluation, quality and security requirements](docs/OPERATIONAL_CAPABILITIES.md).

**Product scope:** hybrid public + owner experience, with private memory, task planning and policy-controlled execution delegated to MR_SIAVASH_HUB_AI.

## Trust boundaries

- EL_AI owns Telegram transport, Telegram identity mapping, command routing and user-facing delivery.
- MR_SIAVASH_HUB_AI remains authoritative for private memory, tasks, agents, approvals, tool policy and audit.
- MR_SIAVASH_AI is a public website and public-knowledge Gateway only.
- No shared database, browser-held Hub credential, public tunnel, or implicit trust between projects.
- Private Hub access uses owner-initiated, Telegram-ID-bound pairing and dedicated scoped API routes.
- Hub credentials are stored in an OS-backed credential manager by default. Headless deployments may explicitly select AES-256-GCM encrypted file storage with a 32-byte key injected by the host secret manager; no plaintext fallback, owner password, or unrestricted Hub session is stored by EL_AI.
- Public website queries send only the current message to the allowlisted public Gateway; private Hub context is never forwarded.

## Current implementation status

The Telegram transport, owner/public separation, scoped Hub client, stable local client identity, OS-backed Keyring storage, and public MR_SIAVASH_AI assistant client are implemented on `main`. Public conversations and Hub access remain opt-in. A separate Cloudflare edge Worker (`mr-siavash-el-ir-bot`) is deployed and its live `/health` and `/ready` endpoints returned healthy responses on 2026-11-22; required secret binding names are present. This does not prove the Telegram token is valid, webhook registration succeeded, the signed bridge is operational, or messages reach the Python command router. The local Python polling runtime remains unconfigured, and end-to-end Telegram acceptance is not claimed.

MR_SIAVASH_AI Cloudflare Gateway deployment details below are historical observations, not fresh live inventory. The Telegram edge Worker is a separate service and is covered by the 2026-11-22 read-only checkpoint in docs/architecture/cloudflare-telegram-edge-bridge.md. The last recorded Production deployment was Worker version `62e8a337-d804-4748-8bb2-d5e0180ee2f8` (version 14), annotated with MR_SIAVASH_AI source commit `1616545a2e204cb378e2c824299412ca20ef598b` and recorded on 2026-11-11. Historical notes describe the production D1 binding as `mr-siavash-ai-prod`, Workers AI configuration, query-string redaction, and a daily `0 3 * * *` UTC cleanup schedule; none of those live settings were re-read in this source refresh. Historical cleanup telemetry recorded `support_retention_cleanup_completed` events on 2026-11-12 and 2026-11-13, including aggregate deletion counts, but those events are not proof of current retention state.

As rechecked on 2026-11-22, Website `main` is `fe48ad35320b5f595c2b14ec0f199967449aa87d`. Website PR [#249](https://github.com/MRSIAVASSHIRBOT/MR_SIAVASH_AI/pull/249) merged a read-only Cloudflare Production/Preview source and privacy audit as this commit. The exact-main Quality/GitHub Pages workflow [44950311746](https://github.com/MRSIAVASSHIRBOT/MR_SIAVASH_AI/actions/runs/44950311746) completed successfully, including the static Pages deployment. Gateway CI [44950311810](https://github.com/MRSIAVASSHIRBOT/MR_SIAVASH_AI/actions/runs/44950311810), Workflow Security Audit [44950311790](https://github.com/MRSIAVASSHIRBOT/MR_SIAVASH_AI/actions/runs/44950311790), OpenSSF Scorecard [44950311821](https://github.com/MRSIAVASSHIRBOT/MR_SIAVASH_AI/actions/runs/44950311821), Production Gateway runtime smoke [44950311773](https://github.com/MRSIAVASSHIRBOT/MR_SIAVASH_AI/actions/runs/44950311773), and Preview Gateway runtime smoke [44950311840](https://github.com/MRSIAVASSHIRBOT/MR_SIAVASH_AI/actions/runs/44950311840) also completed successfully on the same source SHA. GitHub compare reports the current source is **106 commits ahead and 0 behind** the last recorded Production Worker source annotation `1616545a2e204cb378e2c824299412ca20ef598b`; this is source drift, not a deployment claim.

The MR_SIAVASH_AI Production and Preview Gateway versions, D1 state, bindings, routes, secrets, schedules, and traffic weights were not re-read in this verification and remain unverified live facts. Separately, the Telegram edge Worker was inspected read-only: its deployment, secret-binding names, and health/readiness responses are recorded in docs/architecture/cloudflare-telegram-edge-bridge.md. No Telegram webhook registration, Telegram API token validation, D1 schema/data query, bridge pull/complete round-trip, or user-message delivery was performed.

See docs/architecture/overview.md, docs/security/threat-model.md, and docs/roadmap/implementation-plan.md.

Cloudflare Telegram bridge operating procedures and end-to-end acceptance gates are documented in [the bridge runbook](docs/operations/cloudflare-telegram-bridge-runbook.md).

## Development

Python 3.11+.

```bash
python -m venv .venv
# activate the virtual environment, then:
python -m pip install -e ".[dev]"
cp .env.example .env
python -m el_ai
```

The default Python transport is local Telegram long polling. The merged Cloudflare bridge runtime is an alternative: it consumes the signed queue from the deployed `mr-siavash-el-ir-bot` Worker and deliberately does not start `getUpdates`. The Worker has webhook ingress, encrypted queued jobs, update deduplication, and signed bridge pull/complete routes (see [observed Cloudflare edge state](docs/architecture/cloudflare-telegram-edge-bridge.md)). The Python consumer must still be started on a trusted host with Hub reachability and protected credential storage. Local installations use a trusted OS keyring; POSIX headless hosts can use the explicitly configured AES-GCM encrypted file backend. Windows hosts must retain the OS keyring. A deployed Worker and green repository CI do **not** prove Telegram webhook registration, token validity, private Hub pairing, or end-to-end message delivery.

Run the read-only local preflight before configuring or starting the bot:

```bash
el-ai doctor
el-ai doctor --json
# or:
python -m el_ai doctor
```

The preflight does not contact Telegram, Hub, or the public Gateway, does not write files, and never prints credentials. It checks required environment settings, local state-directory access, Hub URL policy and trusted keyring availability (only when Hub integration is enabled). It does not prove that a Telegram token is valid or that remote services are reachable. See [docs/operations/preflight.md](docs/operations/preflight.md).

## Configuration

- TELEGRAM_BOT_TOKEN: required only for `TELEGRAM_TRANSPORT=local_polling`; the Cloudflare Worker owns Telegram ingress and delivery in bridge mode.
- TELEGRAM_TRANSPORT: `local_polling` (default) or `cloudflare_bridge`. Select exactly one; bridge mode never starts the local Telegram update poller.
- TELEGRAM_BRIDGE_URL: HTTPS base URL of the Cloudflare Telegram Worker; required in bridge mode.
- TELEGRAM_BRIDGE_SHARED_SECRET: bridge HMAC secret, at least 32 characters; keep it only in the local runtime secret environment and Cloudflare secret binding, never in Git.
- TELEGRAM_BRIDGE_TIMEOUT_SECONDS: bridge HTTP timeout from 3 to 30 seconds (default 8).
- TELEGRAM_OWNER_IDS: comma-separated numeric Telegram user IDs allowed to use owner commands.
- TELEGRAM_PUBLIC_ENABLED: whether public conversations are enabled (default false).
- EL_AI_ENVIRONMENT: development, test or production.
- EL_AI_STATE_DIR: local state directory containing a non-secret, stable client UUID.
- HUB_API_BASE_URL: local Hub URL (example: http://127.0.0.1:8000); remote URLs are rejected by default. A Render background worker may use a bare single-label internal `host:port` only when the private-network flag below is explicitly enabled.
- HUB_INTEGRATION_ENABLED: opt-in for paired Hub commands; defaults to false.
- HUB_ALLOW_REMOTE: defaults to false; enabling remote Hub URLs requires a separate hosting/security decision and HTTPS.
- HUB_ALLOW_RENDER_PRIVATE_NETWORK: defaults to false; when enabled, accepts only Render's bare private `host:port` binding. Public DNS names, IP literals, localhost, and arbitrary HTTP URLs remain rejected.
- Bridge mode additionally requires a non-empty `TELEGRAM_OWNER_IDS` allowlist, `HUB_INTEGRATION_ENABLED=true`, a reachable Hub URL, and protected credential storage. The Python owner ID must match the Worker’s `ALLOWED_TELEGRAM_USER_ID`.
- SITE_PUBLIC_API_BASE_URL: public-only MR_SIAVASH_AI Gateway URL.
- SITE_PUBLIC_WEB_BASE_URL: base URL used to validate and render public source links.

## Zero-cost local operation

The default zero-cost topology keeps `MR_SIAVASH_HUB_AI` and the EL_AI Python consumer on the same owner-controlled Windows PC. Start the Hub with `scripts/start-local.ps1`; keep its API loopback-only at `http://127.0.0.1:8000`. For Cloudflare bridge mode, the local consumer pulls signed jobs over outbound HTTPS and calls the Hub over loopback. No public tunnel or inbound access to the PC is required.

For this mode, set `HUB_API_BASE_URL=http://127.0.0.1:8000`, keep `HUB_ALLOW_REMOTE=false`, retain the Windows OS Keyring, and set `TELEGRAM_TRANSPORT=cloudflare_bridge`, `HUB_INTEGRATION_ENABLED=true`, the Worker URL, a shared bridge secret, and the matching owner-ID allowlist. The bridge consumer does not need a local Telegram Bot API token; the edge Worker owns Telegram ingress and delivery. See the [zero-cost bridge runbook](docs/operations/cloudflare-telegram-bridge-runbook.md) for the exact configuration and restart procedure.

When the PC is offline, local Hub execution and inference are unavailable. The edge may buffer updates within provider limits, but this is not a 24/7 availability guarantee. Do not expose the local Hub API publicly, configure a paid Render service, or enable a paid model fallback for the zero-cost profile.

## Owner Telegram commands

- `/pairinfo`: show the numeric Telegram ID and stable EL_AI client UUID needed to create a Hub pairing challenge.
- `/pair CODE`: exchange a five-minute, one-time Hub pairing code and store credentials in the OS credential vault.
- `/owner`: confirm that the sender is the configured owner.
- `/hub`: check the paired Hub connection.
- `/tasks [STATUS] [CURSOR]`: list this paired bot's tasks with bounded cursor-based pagination.
- `/task TYPE | DESCRIPTION`: submit a task through Hub policy and idempotency controls.
- `/taskstatus TASK_ID`, `/taskhistory TASK_ID [CURSOR]`, and `/taskresult TASK_ID`: inspect the status, paged event history, or final result of a task submitted by this paired client.
- `/taskprogress TASK_ID`: view a compact status summary and the latest safe lifecycle events; audit metadata and private payloads are not included.
- `/taskplan TYPE | STEP 1 ; STEP 2`: save a bounded owner-authored plan preview locally. Sequential shorthand remains supported; explicit 1-based dependencies use `1: Collect ; 2: Validate <- 1 ; 3: Publish <- 1,2`. Plans contain 2–8 non-empty steps, and preview creates no Hub task.
- `/agentplan [local|remote] TYPE | GOAL`: generate a bounded AI plan preview through Hub and save it locally; local inference is the default, remote Kimi is explicit, and no task is created or executed during preview. Validated dependency metadata is preserved when returned by the planner.
- `/taskplanrun PLAN_ID`: explicitly start a saved plan. Only root steps are initially submitted as separate policy-gated Hub tasks; dependent steps are submitted automatically only after every prerequisite reports `succeeded`. Advancement is bounded, checkpointed, and uses stable idempotency keys for ambiguous network outcomes. Failed or blocked prerequisites block descendants; unavailable status never unlocks a step.
- `/taskplans`: list the owner's saved plans and per-plan submission counts. Local plan drafts expire after seven days and are stored in a private state file; do not include credentials in step text.
- `/taskplanstatus PLAN_ID`: inspect each step, its dependencies, and persisted blocked reason without exposing task result payloads.
- `/taskplanwatch PLAN_ID`: add active submitted steps in the plan to owner-only progress notifications.
- `/taskwatch TASK_ID` notifications now include a bounded lifecycle timeline on status transitions; if event history is temporarily unavailable, the status notification still goes through.
- `/taskcancel TASK_ID`: cancel a task only while it is queued or waiting for approval.
- `/taskwatch TASK_ID`, `/taskwatches`, and `/taskunwatch TASK_ID`: persist, list, or stop owner-only task status notifications. Watches store task IDs, Telegram owner IDs, status checkpoints, and bounded retry deadlines only; transient Hub or Telegram failures use persisted exponential backoff (30 seconds to 30 minutes). Delivery remains at-least-once when Telegram reports an unknown outcome.
- `/memory QUERY`: search the owner's Hub memory.
- `/memoryentry MEMORY_ID`: inspect one active owner-owned entry before approving it.
- `/memoryapprove MEMORY_ID`: explicitly mark one reviewed entry as user-approved. This requires the separately granted `memory:write` scope and cannot edit or delete memory.
- `/memorydream`: run a bounded, read-only duplicate-memory review; every proposal requires owner review and no entry is merged or deleted.
- `/memorycheck`: run a bounded, read-only review for possible same-category polarity conflicts, expired notes, and unapproved or low-confidence entries. Results are heuristic hints, not factual verification, and no memory is changed.
- `/videoproviders`: inspect the non-secret configured video provider catalog.
- `/videoroute SECONDS ASPECT [720p|1080p] [remote-ok spend-ok USD_CAP]`: request a read-only local-first route preview. It starts no generation, submits no prompt or media, and creates no provider charge.
- `/videohistory [CURSOR]` and `/videostatus GENERATION_ID`: inspect bounded, owner-scoped video generation history and status.
- `/videocancel GENERATION_ID`: cancel only a prepared or queued generation before provider submission; active or ambiguous provider work is not reported as cancelled.
- `/videodrafts ANALYSIS_ID`: list up to five owner-scoped storyboard drafts and whether each has a saved renderer-compatible timeline.
- `/videorender DRAFT_ID`: submit a deterministic local FFmpeg render from a prepared draft. Telegram update IDs are used for idempotent retries; the Hub freezes the exact draft revision into an immutable snapshot.
- `/videorenders [CURSOR]`: list recent local renders with owner-scoped cursor pagination.
- `/videorenderstatus RENDER_ID` and `/videorendercancel RENDER_ID`: inspect or cancel the local render.
- `/videorenderdownload RENDER_ID`: explicitly send a completed private MP4 to the owner’s current private Telegram chat. The Hub verifies ownership, MP4 signature, SHA-256 and size; uploads above 48 MB remain available through the authenticated Hub dashboard.
- `/videoscenes ASSET_ID [THRESHOLD] [MAX_CHANGES]`: queue bounded local FFmpeg scene-cut detection; `/videoscenestatus JOB_ID` reads timestamp-only results and `/videoscenecancel JOB_ID` cancels an owned job. This does not perform semantic scene recognition or call a remote provider. `/videoscenehistory [CURSOR]` lists owner-scoped jobs with stable pagination, and `/videosceneexport JOB_ID` returns a small CSV of scene start times for use in an editor.
- `/health`: inspect aggregate polling health and counters (owner-only; no message or user data is exposed).
- `/site QUERY`: query public website knowledge without sending private Hub context.

Video catalog, route preview, generation history, render-draft listing, render history, render status and private render download require `video:read`. Scene analysis submission separately requires the explicitly granted `video:analyze` scope; scene-job status requires `video:read`, and cancellation requires `video:cancel`. Local deterministic FFmpeg render submission separately requires the opt-in `video:render` scope; cancellation of local renders or pending provider generations requires `video:cancel`. Render submission never calls a generative provider or sends source media to a remote service. Existing pairings do not receive new scopes automatically; the authenticated Hub owner must explicitly grant `memory:write` for approvals and update the client scope set

When TELEGRAM_PUBLIC_ENABLED=true, public users can send normal text in a private chat and receive answers from the public website Gateway. Group chats remain blocked.

Pairing is owner-initiated from the local Hub. The bot must run in a private Telegram chat. Never send the Hub owner password or access/refresh tokens to Telegram.

For the Cloudflare bridge runtime, configure the bridge variables and Hub settings in the trusted Python host environment, then run the same `python -m el_ai` entrypoint. The Worker forwards a fixed allowlist of owner Hub commands (including memory, task, plan, and video metadata operations) as encrypted dispatch jobs; Python independently validates the private owner identity, exact payload shape, command allowlist, and text bounds before invoking the existing Hub handler. `/pair` remains a separate one-time-code payload and keeps its 32–128 character bound. Background-notification commands (`/taskwatch`, `/taskunwatch`, `/taskwatches`, `/taskplanwatch`) and binary `/videorenderdownload` remain unavailable in bridge mode because the bridge does not yet implement periodic notification delivery or file upload. See [Cloudflare bridge runtime configuration](docs/architecture/cloudflare-telegram-edge-bridge.md).

## Safety

Public users cannot access owner commands or private state. All privileged operations must be explicitly allowlisted by capability and authorized by Hub policy. Sensitive or consequential actions require Hub approval. See the security documentation before enabling integrations.
