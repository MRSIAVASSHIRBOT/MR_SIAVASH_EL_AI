# Operational Capability Framework — Conversational/Telegram Integration

**Status:** Design and integration targets; not a claim of runtime completion.

EL_AI is the conversation and Telegram transport layer. It should apply the user's framework to request handling while delegating private authority to the Hub.

## Capabilities to apply
- **GPI:** Convert natural-language requests into structured task intent, constraints, required outputs, and clarification only for consequential unknowns.
- **AWI:** For permitted tasks, coordinate understanding, plan preview/dispatch, execution, result retrieval, evaluation, safe correction and completion reporting. Do not silently duplicate Hub orchestration.
- **CGI:** Detect unavailable Telegram features, Hub API scopes, tool permissions, missing credentials/configuration, unsupported task types, or incomplete evidence; diagnose and offer safe alternatives.
- **PAIOS:** Keep conversation, project/task references, state, memory scope, tool calls, policy and result delivery coordinated. Hub is authoritative for private state/policy; EL_AI owns Telegram UX and transport.
- **Memory/context:** Use explicitly scoped public and owner-private context, avoid unnecessary retention, and do not put tokens, pairing secrets, private message bodies or raw provider payloads in logs.
- **Research/uncertainty:** Use appropriate research capability for fresh facts, distinguish verified results from assumptions, and cite where user-facing claims need evidence.
- **Quality and recovery:** Idempotent delivery keys, bounded retries, task-state reconciliation, cancellation where supported, graceful shutdown and user-visible partial/failure states.

## Boundaries and acceptance targets
- Owner-private access is identity-bound, paired and scope-limited; revocation and expiry are respected.
- Public Gateway queries transmit only the current disclosed request and public context; no private memory leakage.
- Telegram transport never bypasses Hub policy for private execution.
- Completion messages reflect the actual Hub task outcome and evidence.
- Handle duplicate updates, timeout, lost connection, unavailable Gateway/Hub, revoked grant, malformed result and retry races.
