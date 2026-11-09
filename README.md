# MR_SIAVASH_EL_AI

Advanced Telegram interface for the MR_SIAVASH ecosystem.

**Product scope:** a hybrid public + owner experience with advanced AI, memory, planning and policy-controlled execution delegated to MR_SIAVASH_HUB_AI.

## Trust boundaries

- EL_AI owns Telegram transport, identity mapping, conversation delivery and notifications.
- MR_SIAVASH_HUB_AI remains authoritative for private memory, tasks, agents, approvals, tool policy and audit.
- MR_SIAVASH_AI is a public website and public-knowledge gateway only.
- No shared database, browser-held Hub credential, public tunnel, or implicit trust between projects.
- Private Hub integration stays disabled until a reviewed owner-pairing and scoped-credential contract is implemented.

## Current implementation status

The first secure transport foundation is implemented in this branch: owner-only private-chat handling by default, strict configuration validation, bounded Telegram long polling, sanitized API errors, and offline tests. The AI provider, persistent memory, planning and action execution are not connected yet.

A working bot requires an owner-managed Telegram Bot API token supplied through the runtime secret store. Never commit or paste tokens into source control or chat.

## Development

Python 3.11+.

~~~bash
python -m venv .venv
# activate the virtual environment, then:
python -m pip install -e ".[dev]"
cp .env.example .env
python -m el_ai
~~~

The bot uses long polling for local development. Production webhook deployment is a separate release milestone and must use a secret-validated endpoint and TLS.

## Configuration

- TELEGRAM_BOT_TOKEN: required runtime secret.
- TELEGRAM_OWNER_IDS: comma-separated numeric Telegram user IDs allowed to use owner commands.
- TELEGRAM_PUBLIC_ENABLED: whether the limited public help interface is enabled (default false).
- EL_AI_ENVIRONMENT: development, test or production.
- TELEGRAM_POLL_TIMEOUT_SECONDS: long-poll wait, 1–50 seconds.
- TELEGRAM_REQUEST_TIMEOUT_SECONDS: HTTP timeout; must exceed the polling timeout.
- HUB_API_BASE_URL and HUB_SCOPED_TOKEN: reserved for a future reviewed pairing flow; not read or used by this transport milestone. Do not use a Hub owner password here.
- SITE_PUBLIC_API_BASE_URL: reserved for a future public-only website integration.

## Safety

Public users cannot access owner commands or private state. The transport foundation does not persist messages, call an LLM, invoke tools, or execute actions. All future privileged operations must be explicitly allowlisted by capability and authorized by Hub policy. Consequential actions require Hub approval.
