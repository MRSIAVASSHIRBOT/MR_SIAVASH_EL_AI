# MR_SIAVASH_EL_AI

Advanced Telegram interface for the MR_SIAVASH ecosystem.

**Product scope:** hybrid public + owner experience, with advanced AI, memory, planning and policy-controlled execution delegated to MR_SIAVASH_HUB_AI.

## Trust boundaries

- EL_AI owns Telegram transport, Telegram identity mapping, command routing and user-facing delivery.
- MR_SIAVASH_HUB_AI remains authoritative for private memory, tasks, agents, approvals, tool policy and audit.
- MR_SIAVASH_AI is a public website and public-knowledge gateway only.
- No shared database, browser-held Hub credential, public tunnel, or implicit trust between projects.
- Private Hub access uses owner-initiated, Telegram-ID-bound pairing and dedicated scoped API routes.
- Hub credentials are stored in an OS-backed credential manager; no owner password or unrestricted Hub session is stored by EL_AI.

## Current implementation status

The Telegram transport, private-chat-only owner/public separation, scoped Hub client, stable local client identity, and OS-backed Keyring credential storage are implemented on `main`. Hub access is opt-in: the client is initialized and `/pair` is accepted only when `HUB_INTEGRATION_ENABLED=true` and `HUB_API_BASE_URL` points to a valid local Hub endpoint. Repository CI does not verify a live Telegram connection or production bot deployment; runtime credentials must be supplied locally and never committed.

See `docs/architecture/overview.md`, `docs/security/threat-model.md`, and `docs/roadmap/implementation-plan.md`.

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

- `TELEGRAM_BOT_TOKEN`: required runtime secret.
- `TELEGRAM_OWNER_IDS`: comma-separated numeric Telegram user IDs allowed to use owner commands.
- `TELEGRAM_PUBLIC_ENABLED`: whether public conversations are enabled (default false).
- `EL_AI_ENVIRONMENT`: development, test or production.
- `EL_AI_STATE_DIR`: local state directory containing a non-secret, stable client UUID.
- `HUB_API_BASE_URL`: local Hub URL (example: `http://127.0.0.1:8000`); remote URLs are rejected by default.
- `HUB_INTEGRATION_ENABLED`: opt-in for paired Hub commands; defaults to false.
- `HUB_ALLOW_REMOTE`: defaults to false; enabling remote Hub URLs requires a separate hosting/security decision and HTTPS.
- `SITE_PUBLIC_API_BASE_URL`: optional public-only MR_SIAVASH_AI Gateway URL.

Hub access and refresh credentials are stored in the operating-system credential manager through Keyring. Never put Hub passwords, access tokens or refresh tokens in `.env`, source control or Telegram messages.

## Owner Telegram commands

- `/pairinfo`: show the numeric Telegram ID and stable EL_AI client UUID needed to create a Hub pairing challenge.
- `/pair CODE`: exchange a five-minute, one-time Hub pairing code and store credentials in the OS credential vault.
- `/hub`: check the paired Hub connection.
- `/tasks`: list recent Hub tasks.
- `/memory QUERY`: search the owner's Hub memory.
- `/task TYPE | DESCRIPTION`: submit a task through Hub policy and idempotency controls.

Pairing is owner-initiated from the local Hub. The bot must run in a private Telegram chat. Never send the Hub owner password or access/refresh tokens to Telegram.

## Safety

Public users cannot access owner commands or private state. All privileged operations must be explicitly allowlisted by capability and authorized by Hub policy. Sensitive or consequential actions require Hub approval. See the security documentation before enabling integrations.
