# MR_SIAVASH_EL_AI

Advanced Telegram interface for the MR_SIAVASH ecosystem.

**Product scope:** hybrid public + owner experience, with advanced AI, memory, planning and policy-controlled execution delegated to MR_SIAVASH_HUB_AI.

## Trust boundaries

- EL_AI owns Telegram transport, identity mapping, conversation delivery and notifications.
- MR_SIAVASH_HUB_AI remains authoritative for private memory, tasks, agents, approvals, tool policy and audit.
- MR_SIAVASH_AI is a public website and public-knowledge gateway only.
- No shared database, browser-held Hub credential, public tunnel, or implicit trust between projects.
- Private Hub integration stays disabled until a reviewed owner-pairing and scoped-credential contract is implemented.

## Current implementation status

This repository is being bootstrapped from an empty repository. The first milestone is a secure, testable Telegram transport foundation. A working bot requires an owner-managed Telegram Bot API token supplied through the runtime secret store; never commit or paste tokens into source control or chat.

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
- `EL_AI_ENVIRONMENT`: development or production.
- `HUB_API_BASE_URL` and `HUB_SCOPED_TOKEN`: reserved for a future reviewed pairing flow; private integration remains disabled unless both are configured. Do not use a Hub owner password here.
- `SITE_PUBLIC_API_BASE_URL`: optional public-only MR_SIAVASH_AI Gateway URL.

## Safety

Public users cannot access owner commands or private state. All privileged operations must be explicitly allowlisted by capability and authorized by Hub policy. Sensitive or consequential actions require Hub approval. See the security documentation before enabling integrations.
