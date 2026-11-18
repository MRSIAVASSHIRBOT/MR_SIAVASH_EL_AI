# Portfolio Boundaries — 2026-11-18

## Scope
This repository is active and may evolve as the Telegram/public assistant, but it must remain opt-in with the Hub.

## Backlog repositories
`MR_SIAVASH_EL_IR_BOT` and `MR_SIAVASH_IR_H_BOT` are discovery-only. Do not activate, deploy, or integrate them from this repository.

## Safety invariants
1. Never log bot tokens, pairing secrets, message bodies, user identifiers, or raw provider payloads.
2. Read-only diagnostics must not mutate files, network state, Hub memory, Telegram state, or Cloudflare resources.
3. Any Hub request must carry an explicit scope and request identifier.
4. Live Telegram/webhook activation is a separate owner-approved operation.

## Verification checklist
- CI passes on the current head.
- The requested scope is explicit and bounded.
- Production and Backlog boundaries are preserved.
- No PostZen publishing is triggered by repository maintenance.
