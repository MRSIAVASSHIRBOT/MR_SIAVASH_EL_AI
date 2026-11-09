# ADR-0001: Three-project system boundaries

- Status: Accepted
- Date: 2026-11-09

## Context

The Telegram bot must connect the public website and private Hub without duplicating their responsibilities or collapsing their security boundaries.

## Decision

- EL_AI owns Telegram transport, update ingestion, command routing, user-facing formatting and notifications.
- HUB_AI is authoritative for owner identity, private memory, knowledge, tasks, files, Agent runs, tool permissions, approvals and audit.
- MR_SIAVASH_AI owns public identity, learning content, public project metadata and public Gateway services.
- Public and private data are never placed in a shared database.
- The public Gateway is not a tunnel or proxy into private Hub APIs.
- The bot is owner-only by default; public access is an explicit opt-in and limited to public capabilities.

## Consequences

The first milestone can ship a safe Telegram foundation independently. Private Agent execution waits for a reviewed pairing protocol and Hub-side scoped authorization.
