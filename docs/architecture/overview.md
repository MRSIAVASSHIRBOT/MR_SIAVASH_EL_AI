# Architecture overview

## Responsibility split

MR_SIAVASH_EL_AI is the Telegram transport and interaction boundary. It must not become a second owner of private memory, task state, policy, approvals or execution history.

- Telegram adapter: receives updates and sends replies.
- Application layer: validates message shape, private-chat context and sender identity, then applies owner/public policy.
- Polling runtime: maintains an in-memory update offset and retries transient API failures with bounded backoff.
- Future Hub adapter: may submit scoped, typed requests only after an owner-pairing contract is reviewed and implemented.
- MR_SIAVASH_AI: public website and public knowledge only; it is not an authority for private Hub state.

## Current request flow

Telegram update -> structural validation -> private-chat guard -> sender authorization -> allowlisted command handler -> Telegram reply.

The initial release implements only /start, /help, /status and owner-only /whoami. It does not call an LLM, store messages, create tasks, or execute tools.

## Runtime decisions

- Python 3.11+ and asynchronous HTTP.
- Long polling for local development; webhook support is a separate release milestone.
- Owner-only mode is the default. Public mode exposes only limited help and status.
- API errors are sanitized because the Telegram Bot API token appears in the request path.
- The transport has no local persistent memory. Hub remains the source of truth for memory and audit.

## Future agent boundary

Planning, memory retrieval and action proposals belong to MR_SIAVASH_HUB_AI. EL_AI may render approved proposals and collect explicit owner confirmation, but it must not independently authorize or execute a privileged action. A future integration must use a narrowly scoped server-side credential, audience restriction, replay protection, action-bound approvals and an auditable request identifier.
