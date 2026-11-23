# Coordinated repository checkpoint — 2026-11-23

**Evidence scope:** GitHub source, pull-request and Actions metadata read on 2026-11-23 UTC. This is not a live Cloudflare control-plane inventory, Telegram API acceptance, Hub staging test, or physical-device test.

## Default-branch snapshots

| Repository | Default branch | Observed `main` SHA | Latest relevant source evidence |
|---|---|---|---|
| [MR_SIAVASH_HUB_AI](https://github.com/MRSIAVASSHIRBOT/MR_SIAVASH_HUB_AI) | `main` | `3ec1711b223a17c1ab4b134fdd8d0ec3112c8a78` | Startup recovery now reconciles stale work before worker dispatch; the stop latch does not suppress stale-work recovery. |
| [MR_SIAVASH_AI](https://github.com/MRSIAVASSHIRBOT/MR_SIAVASH_AI) | `main` | `b4f6f49c68cff907f18b04de6f02f7217541f552` | The Gateway request boundary now contains unexpected asynchronous failures; PR #257 is merged. |
| [MR_SIAVASH_EL_AI](https://github.com/MRSIAVASSHIRBOT/MR_SIAVASH_EL_AI) | `main` | `02ec4022f572f97fbf31ce16d418ab61a9119566` | Telegram edge owner-command dispatch and its D1 mock regression test are on main. |

## Exact-main CI evidence at capture

### Hub

The following seven main-branch runs were queued when inspected; no green result is claimed for this SHA yet:

- [Backend CI #45164553796](https://github.com/MRSIAVASSHIRBOT/MR_SIAVASH_HUB_AI/actions/runs/45164553796)
- [CI #45164553862](https://github.com/MRSIAVASSHIRBOT/MR_SIAVASH_HUB_AI/actions/runs/45164553862)
- [Workflow Security Audit #45164553859](https://github.com/MRSIAVASSHIRBOT/MR_SIAVASH_HUB_AI/actions/runs/45164553859)
- [OpenSSF Scorecard #45164553868](https://github.com/MRSIAVASSHIRBOT/MR_SIAVASH_HUB_AI/actions/runs/45164553868)
- [Defensive Python Scan #45164553887](https://github.com/MRSIAVASSHIRBOT/MR_SIAVASH_HUB_AI/actions/runs/45164553887)
- [Video Render Integration #45164553805](https://github.com/MRSIAVASSHIRBOT/MR_SIAVASH_HUB_AI/actions/runs/45164553805)
- [Windows Launcher Validation #45164553854](https://github.com/MRSIAVASSHIRBOT/MR_SIAVASH_HUB_AI/actions/runs/45164553854)

Hub PR [#569](https://github.com/MRSIAVASSHIRBOT/MR_SIAVASH_HUB_AI/pull/569) is a one-file FFmpeg CI mirror-normalization fix. Its branch received a follow-up correction to preserve YAML block indentation; the updated head is `50d5c066952442ecfdb65fd4ee8d7173608fe9d2`. Its exact-head checks were still queued or running at capture, so it is not approved for merge. Other high-impact open candidates include [#544](https://github.com/MRSIAVASSHIRBOT/MR_SIAVASH_HUB_AI/pull/544) (server-authoritative policy inspection) and [#546](https://github.com/MRSIAVASSHIRBOT/MR_SIAVASH_HUB_AI/pull/546) (tool-definition validation and replay-safety metadata); their exact-head check suites were not complete at capture.

### Website

On `b4f6f49c68cff907f18b04de6f02f7217541f552`:

- [Gateway CI #45167111616](https://github.com/MRSIAVASSHIRBOT/MR_SIAVASH_AI/actions/runs/45167111616) — success.
- [Production Gateway Runtime Smoke #45167111539](https://github.com/MRSIAVASSHIRBOT/MR_SIAVASH_AI/actions/runs/45167111539) — success.
- [Preview Gateway Runtime Smoke #45167111419](https://github.com/MRSIAVASSHIRBOT/MR_SIAVASH_AI/actions/runs/45167111419) — success.
- [Workflow Security Audit #45167112491](https://github.com/MRSIAVASSHIRBOT/MR_SIAVASH_AI/actions/runs/45167112491) — success.
- [OpenSSF Scorecard #45167111475](https://github.com/MRSIAVASSHIRBOT/MR_SIAVASH_AI/actions/runs/45167111475) — success.
- [Quality / GitHub Pages #45167112487](https://github.com/MRSIAVASSHIRBOT/MR_SIAVASH_AI/actions/runs/45167112487) — queued at capture; publication is not claimed for this SHA.

The remaining open Website work is organized as phase-based and stacked PRs. Do not merge a downstream phase before its declared base branch is integrated and the resulting exact-head checks pass. The static site and public Gateway remain separate from private Hub state.

### EL_AI

All four observed main-branch checks succeeded on `02ec4022f572f97fbf31ce16d418ab61a9119566`:

- [CI #45076563125](https://github.com/MRSIAVASSHIRBOT/MR_SIAVASH_EL_AI/actions/runs/45076563125) — success.
- [Telegram Edge Worker Tests #45076563112](https://github.com/MRSIAVASSHIRBOT/MR_SIAVASH_EL_AI/actions/runs/45076563112) — success.
- [Workflow Security Audit #45076563209](https://github.com/MRSIAVASSHIRBOT/MR_SIAVASH_EL_AI/actions/runs/45076563209) — success.
- [OpenSSF Scorecard #45076563181](https://github.com/MRSIAVASSHIRBOT/MR_SIAVASH_EL_AI/actions/runs/45076563181) — success.

EL_AI PR [#110](https://github.com/MRSIAVASSHIRBOT/MR_SIAVASH_EL_AI/pull/110) remains Draft. Its finite timeout/backoff validation has 3/3 exact-head checks passing, but draft status is retained pending maintainer review and explicit promotion.

## Cross-repository contract

The approved Hub manifest is the canonical v1 private integration contract: [contracts/hybrid/v1/manifest.json](https://github.com/MRSIAVASSHIRBOT/MR_SIAVASH_HUB_AI/blob/main/contracts/hybrid/v1/manifest.json).

- Hub owns private state, identity, scoped authorization, policy, approvals, task execution and audit.
- EL_AI owns Telegram transport, identity mapping, command routing and delivery. Private Hub calls require owner-initiated, Telegram-ID-bound pairing and scoped bearer credentials.
- MR_SIAVASH_AI owns public presentation, public learning content and public support/Gateway routes. EL_AI sends only the current public message to the public assistant; it must not forward Telegram IDs, private history, Hub credentials, memory or task data.
- No shared database, browser-held Hub credential, public tunnel, implicit trust, or public-Gateway route into private Hub capabilities is permitted.
- The public Gateway request ID and private Hub correlation ID are separate diagnostic contexts, not authorization or idempotency credentials.

## Security and operational limits

- GitHub reports `main.protected=false` for all three repositories. No branch-protection, ruleset, permission or access-control setting was changed. An owner/admin must configure and verify required current-head checks.
- No secret values were read or copied. This checkpoint contains no credentials.
- Cloudflare Worker versions, bindings, D1 state, routes, schedules, secrets and traffic were not re-read during this GitHub maintenance run.
- Telegram token validity, webhook registration, live bridge pull/complete, Hub staging round-trips, and physical Windows/Android acceptance remain unverified.
- GitHub Actions smoke success does not prove a new Cloudflare deployment or live Telegram message delivery.

## Prioritized next steps

1. Complete the updated exact-head CI for Hub PR #569; merge only if every required check passes and the YAML workflow is parsed and executed.
2. Complete and review Hub PRs #544 and #546 independently; preserve server-side policy authority and fail-closed tool registration.
3. Wait for Website Quality / Pages on the current main SHA and keep phase-stacked PRs in dependency order.
4. Review EL_AI PR #110, then promote it from Draft only after the owner/maintainer is satisfied with the bounded timing contract.
5. Perform isolated Hub–EL_AI acceptance with a test bot and non-production resources; do not use production Telegram or Cloudflare resources as a test target.
6. Have the repository owner apply branch protection and verify that pending/failing current-head checks block merging.
