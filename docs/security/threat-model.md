# Security and privacy threat model

## Assets

- Telegram Bot API token.
- Owner Telegram numeric IDs.
- Private conversation metadata and message content.
- Future Hub scoped credential and approval references.

## Trust boundaries

- Telegram is an external message transport.
- EL_AI is an untrusted-input boundary, not an authorization authority for Hub operations.
- Hub is authoritative for private memory, tasks, agents, approvals, policy and audit.
- Public website endpoints provide public information only.

## Initial controls

- Token is loaded from runtime environment or a local ignored .env file; it is never committed.
- Settings hide the token from object representations.
- API exceptions omit token-bearing URLs, response bodies and provider descriptions.
- Owner IDs are parsed strictly; unknown senders are silent unless limited public mode is explicitly enabled.
- Only private chats are processed. Group messages are ignored.
- Public mode defaults off and does not expose owner commands.
- No messages are persisted or forwarded to an LLM in this milestone.
- No shell, filesystem, browser, Cloudflare, GitHub or arbitrary tool access is present.
- HTTP polling timeout is bounded and retry backoff is capped.

## Threats deferred to later milestones

- Webhook spoofing: not applicable to long polling; webhook deployment must validate Telegram's secret token and use TLS.
- Duplicate update delivery: current offset is in memory and may replay harmless read-only commands after restart. Any future state-changing command needs durable idempotency.
- Prompt injection and tool abuse: no LLM or tool execution is connected yet. Future agent inputs must be treated as untrusted data and policy-checked by Hub.
- Credential theft through process inspection: environment secrets remain visible to the runtime process; production deployment must use a dedicated secret store and least-privilege service identity.
- Cross-project impersonation: no Hub credential or pairing flow is enabled.

## Release gates

Do not enable public conversations, webhook delivery, private Hub integration, persistent memory, or action execution until the corresponding threat model, tests, owner consent and rollback plan are reviewed.
