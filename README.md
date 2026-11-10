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

The production website Gateway currently has an active Workers AI binding and support-retention configuration. Its 100% traffic deployment is Worker version `146709f7-8158-40c8-aa80-b8b0772787f1`, built from MR_SIAVASH_AI commit `971a5e702876a0bc78b19c55b8b0daf358471c5b`; production release smoke run `42801780384` passed on 2026-11-10, including the grounded assistant path. The newer MR_SIAVASH_AI main commit `75acc9ed4e264b0086bb98b220e330ecd8578a97` adds aggregate-only retention telemetry and is not yet deployed, so no successful telemetry event from that version is claimed. This describes the website Gateway only; it does not mean the Telegram bot is connected or deployed.

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

- /pairinfo: show the numeric Telegram ID and stable EL_AI client UUID needed to create a Hub pairing challenge.
- /pair CODE: exchange a five-minute, one-time Hub pairing code and store credentials in the OS credential vault.
- /hub: check the paired Hub connection.
- /tasks: list tasks submitted by this paired bot only.
- /memory QUERY: search the owner's Hub memory.
- /task TYPE | DESCRIPTION: submit a task through Hub policy and idempotency controls.
- /site QUERY: query public website knowledge without sending private Hub context.

When TELEGRAM_PUBLIC_ENABLED=true, public users can send normal text in a private chat and receive answers from the public website Gateway. Group chats remain blocked.

Pairing is owner-initiated from the local Hub. The bot must run in a private Telegram chat. Never send the Hub owner password or access/refresh tokens to Telegram.

## Safety

Public users cannot access owner commands or private state. All privileged operations must be explicitly allowlisted by capability and authorized by Hub policy. Sensitive or consequential actions require Hub approval. See the security documentation before enabling integrations.
