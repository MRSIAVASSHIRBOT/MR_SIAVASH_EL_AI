# Operations Runbook

## Local development

1. Install Python 3.11 or newer.
2. Create and activate a virtual environment.
3. Install `pip install -e ".[dev]"`.
4. Copy `.env.example` to `.env` and set the bot token locally.
5. Set `TELEGRAM_OWNER_IDS` to the numeric ID of the owner.
6. Keep `TELEGRAM_PUBLIC_ENABLED=false` until public capabilities have been reviewed.
7. Start with `python -m el_ai`.

## Secret handling

Never commit `.env`, bot tokens, Hub credentials or provider keys. Rotate a token immediately if exposed. Do not send tokens through Telegram messages or chat.

## Shutdown and failure

The polling loop handles SIGINT/SIGTERM and uses bounded exponential backoff for Telegram API failures. It does not blindly retry a send whose provider outcome may be ambiguous.

## Production

Production deployment is not included in the bootstrap. Before webhook deployment, define TLS termination, exact webhook secret validation, request size/rate limits, replay/update deduplication, monitoring, log retention and rollback. Never expose the local Hub API or create a public tunnel to a development machine.
