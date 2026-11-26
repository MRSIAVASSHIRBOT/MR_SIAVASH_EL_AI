# Three-repository coordinated checkpoint — 2026-11-26

**Evidence boundary:** GitHub repository metadata, recent commits, open issues/PRs, source files, dependency manifests, workflow definitions, versioned contract fixtures and exact-commit Actions runs observed on 2026-11-26. This is a source/CI checkpoint, not a fresh Cloudflare, Render, Telegram, Hub-staging or physical-device acceptance report.

## 1. Current source and CI snapshot

| Repository | Default branch | Main SHA | Main workflow runs observed | Open PRs |
|---|---|---|---|---|
| [MR_SIAVASH_HUB_AI](https://github.com/MRSIAVASSHIRBOT/MR_SIAVASH_HUB_AI) | `main` | `f2943e4b68aa95051bba02a53aacf6b3de461521` | 3 successful; 4 in progress at capture | [#618](https://github.com/MRSIAVASSHIRBOT/MR_SIAVASH_HUB_AI/pull/618) Draft |
| [MR_SIAVASH_AI](https://github.com/MRSIAVASSHIRBOT/MR_SIAVASH_AI) | `main` | `4578392422fa5a2430caec0007e4ba6cf5fd596c` | 6 successful | None |
| [MR_SIAVASH_EL_AI](https://github.com/MRSIAVASSHIRBOT/MR_SIAVASH_EL_AI) | `main` | `6e3e6199ba0a506e40fe2f850e6fe5ce625e3c08` | 3 successful | None |

All three repositories report `main.protected=false`. No branch-protection, ruleset, collaborator, secret, or access-control setting was changed.

Hub's latest merge during this checkpoint was PR [#635](https://github.com/MRSIAVASSHIRBOT/MR_SIAVASH_HUB_AI/pull/635), merged as `f2943e4b68aa95051bba02a53aacf6b3de461521`. Its documentation-only exact head `c56d8bf6e9b1d4828c3cecb0eeba1d92ac8e1bff` passed all five observed PR workflow runs before merge. The post-merge Hub main workflows were still running at capture; do not describe Hub main as fully green until all seven observed runs complete.

## 2. Recent implementation evidence and scope

### Hub

- H10 added a digest-bound, read-only WorkOrder evidence foundation in `backend/mrhub/agents/work_order_evidence.py`, with 13 focused tests in `backend/tests/test_work_order_evidence.py`.
- The evaluator binds reports to a WorkOrder ID, revision and SHA-256 digest; evidence can pass only through an exact trusted server-side (source_type, verifier_id) registry entry.
- Missing, unregistered, stale, conflicting, malformed or unverifiable evidence cannot produce acceptance. The report is read-only and cannot execute, retry, mutate permissions or approve actions.
- ADR-0046 and `docs/operations/h10-workorder-evidence-v1.md` explicitly state that production verifier adapters, an owner-scoped API route and EL_AI integration are not implemented.
- H15 traceability documentation was corrected in PR #635. Hub-local task-bound traces are implemented; distributed Bridge-job-to-Hub-task correlation and any separate top-level operation ID remain open design/integration work.

The H10 source commit `7cbfaf2150a1d6330d824020e550bb6a372584d9` passed the seven observed main workflow runs on that commit. The later PR #635 merge started a new mainline run set, which was not complete at capture.

### Website

- A12 consented, identifier-minimized aggregate analytics and A13 deterministic first-party static-payload budgets are in source.
- Main SHA `4578392` has six successful workflow runs: Quality/Pages, Gateway CI, Production and Preview Runtime Smoke, Workflow Security Audit and OpenSSF Scorecard.
- Successful Runtime Smoke is not proof that the latest source is active in Cloudflare traffic.

### EL_AI

- E8 task-plan schema v5 stores bounded dispatch retry checkpoints.
- E8 remains execution-coverage-only: a terminal task result does not prove semantic acceptance of a goal or criterion.
- The current `telegram_agent_plan.v1` consumer contract remains unchanged.
- The latest E8 documentation explicitly distinguishes execution evidence from goal satisfaction and states that `goal_satisfaction` remains `not_evaluated`.

## 3. Cross-repository contract

The Hub canonical file `contracts/hybrid/v1/manifest.json` and EL_AI fixture `tests/fixtures/hub-hybrid-manifest-v1.json` have identical Git blob SHA:

`e6c7ab219ccf0d9fd0613d09f8c12b2c8d1fdcdd`

This confirms source-fixture equality for v1 at this checkpoint; it does not prove a live Hub/EL_AI session.

The integration boundaries remain:

- Hub owns private memory, task state, policy decisions, approvals, tool grants and audit.
- EL_AI owns Telegram transport, Telegram identity mapping, command routing and user-facing delivery.
- Website remains public-only and cannot receive private Hub credentials, memory or owner context.
- No shared database, ambient trust, or browser-held Hub credential is introduced.
- Correlation IDs are diagnostic metadata only; they are not credentials, authorization grants or idempotency keys.

### H10-to-E8 compatibility gate

H10 is not yet a consumer-facing contract. No H10 verifier adapter, public evaluation route, canonical WorkOrder digest in a versioned planning response, or EL_AI E8 report consumer was observed in the current source.

Before semantic acceptance is enabled:

1. Define a canonical, immutable WorkOrder serialization and digest/revision contract.
2. Add trusted Hub verifier adapters that independently resolve allow-listed evidence sources and recompute evidence digests.
3. Add an owner-scoped, read-only Hub evaluation route with audit and bounded responses.
4. Introduce a versioned planning extension (for example, a v2 contract) without changing the existing v1 response semantics.
5. Persist the digest/revision with the EL_AI plan and reject stale evidence after plan revision.
6. Add consumer-driven tests for unknown verifier, digest mismatch, stale revision, conflicting evidence, owner isolation, cancellation and retry ambiguity.
7. Keep semantic acceptance disabled until the complete negative authorization and evidence matrix passes.

## 4. Open work and dependencies

### Hub

- [PR #618](https://github.com/MRSIAVASSHIRBOT/MR_SIAVASH_HUB_AI/pull/618) remains Draft. Its base SHA is `46287169cfc0b1825c722e5acba4fac59d371697`, older than current Hub main, and its Render worker still pins EL_AI `57b8289fddbc53c4e7ee1709066939467770f3a0`, older than current EL_AI main `6e3e6199ba0a506e40fe2f850e6fe5ce625e3c08`.
- The proposal declares two paid Starter services and persistent disks. No provisioning is authorized by green CI. Keep it Draft until it is rebased/re-pinned, exact-head checks pass, the owner reviews the complete recurring cost, and Render Workspace Blueprint Auto Sync is independently confirmed disabled.
- [Issue #304](https://github.com/MRSIAVASSHIRBOT/MR_SIAVASH_HUB_AI/issues/304) tracks main branch protection; [#512](https://github.com/MRSIAVASSHIRBOT/MR_SIAVASH_HUB_AI/issues/512) tracks the broader recovery/integration backlog.

### Website

- [Issue #120](https://github.com/MRSIAVASSHIRBOT/MR_SIAVASH_AI/issues/120) tracks reconciliation of Gateway rollback versions with current source and smoke evidence.
- [Issue #70](https://github.com/MRSIAVASSHIRBOT/MR_SIAVASH_AI/issues/70) tracks main branch protection.
- PR #317 is the new documentation-only security-policy correction based on the latest recorded audit; it is not merged in this checkpoint.

### EL_AI

- [Issue #140](https://github.com/MRSIAVASSHIRBOT/MR_SIAVASH_EL_AI/issues/140) proposes a separate permission-scoped Telegram Business message channel. Business contacts must not enter the existing owner-command path or gain Hub pairing, private memory, task creation or project administration. The referenced companion repository/PR is outside this three-repository checkpoint and was not validated here.
- [Issue #107](https://github.com/MRSIAVASSHIRBOT/MR_SIAVASH_EL_AI/issues/107) tracks Telegram/Hub integration.
- [Issue #5](https://github.com/MRSIAVASSHIRBOT/MR_SIAVASH_EL_AI/issues/5) tracks main branch protection.

## 5. Dependencies, workflows and security-sensitive configuration

| Repository | Runtime and direct dependencies | CI/workflow files |
|---|---|---|
| Hub | Python >=3.11; FastAPI, Uvicorn, Pydantic, HTTPX, cryptography; pytest, Ruff, pip-audit, Bandit and MCP dev extras | 7 workflows: backend CI, Bandit, CI, Scorecard, video-render integration, Windows launcher validation, workflow security |
| Website | Static HTML/CSS/JS; Node 22 for Cloudflare Gateway tests; Gateway package uses Node's built-in test runner | 8 workflows: CI, Pages deploy, Preview/Production release and smoke paths, Scorecard, workflow security |
| EL_AI | Python >=3.11; HTTPX, Keyring, python-dotenv, cryptography; pytest, pytest-asyncio and Ruff | 4 workflows: CI, Scorecard, Telegram edge Worker, workflow security |

No Python lockfile was present in the inspected root manifests. Reproducible dependency locking remains follow-up work.

Security-sensitive configuration review covered Hub `.env.example`, `render.yaml`, and `.github/workflows`; Website `SECURITY.md`, Gateway release workflows and Worker package configuration; and EL_AI `.env.example`, threat model, credential-store code and Telegram edge Worker boundary. Environment-variable names were inspected without reproducing values. No secret values were read or copied into this checkpoint.

## 6. Live-service evidence boundary

The latest recorded Cloudflare/D1 read-only audit is dated **2026-11-23 15:48 UTC**, not 2026-11-26. It recorded:

- Production Worker v14 at 100% traffic, source annotation `1616545a2e204cb378e2c824299412ca20ef598b`.
- Preview Worker v10 at 100% traffic, with no repository source annotation.
- Query-string redaction enabled in Production but disabled in Preview.
- Traces enabled at 10% head sampling in Production and disabled in Preview; logs and persistent invocation logs enabled in both.
- Only D1 migrations 0001–0005 in the then-observed Production and Preview ledgers; migrations 0006–0008 were absent.

These are historical observations and require a fresh control-plane/D1 readback before any release. No Cloudflare deployment, D1 migration/write, Render provisioning, Telegram webhook change, production message, credential change or device operation was performed in this checkpoint.

## 7. Prioritized next work

1. **P0 — Finish Hub post-merge CI** on `f2943e4`; inspect failures and confirm all current-head checks before further code integration.
2. **P0 — Keep Render PR #618 gated**; refresh its base and immutable EL_AI pin only after source review, then require owner cost and Auto Sync approval before any manual sync.
3. **P0 — Close the H10/E8 semantic-evidence gap** with trusted Hub verifiers, owner-scoped read-only evaluation, a versioned backward-compatible planning contract and consumer-driven tests. Until then, keep E8 execution-coverage-only.
4. **P0 — Reconcile Cloudflare Preview privacy and D1 state** using a fresh read-only audit; do not deploy or migrate Production without the documented release approvals and backup/restore evidence.
5. **P1 — Complete isolated Telegram/Hub acceptance** for pairing, scope, expiry, revocation, Bridge pull/complete, idempotency and sanitized failures. Do not send production messages.
6. **P1 — Design Telegram Business support separately** from owner commands, with explicit connection rights, private-chat allowlisting, exact reply binding, encrypted queue payloads and ambiguous-delivery quarantine.
7. **P1 — Enforce main branch governance** through owner/admin-approved rulesets and prove pending/failing current-head checks block merges.
8. **P2 — Add reproducible Python dependency locks** and clean-install validation on Linux and Windows.

