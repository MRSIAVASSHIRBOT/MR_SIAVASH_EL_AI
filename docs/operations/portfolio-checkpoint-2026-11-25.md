# Three-repository coordinated checkpoint — 2026-11-25

**Evidence boundary:** GitHub repository metadata, source trees, recent commits, open issues/PRs, file contents, versioned contract fixtures and exact-commit Actions check-runs observed during this checkpoint. This is a source/CI snapshot, not a live Cloudflare, Render, Telegram, Hub-staging or physical-device inventory.

## 1. Current source snapshots

| Repository | Default branch | Main SHA | Exact-main check-runs | Open PRs |
|---|---|---|---:|---|
| [Hub](https://github.com/MRSIAVASSHIRBOT/MR_SIAVASH_HUB_AI) | `main` | `5d3813d974a8e2681d47dea176f04f20546ffb4f` | 9/14 succeeded; 5 in progress at capture | #618 Draft |
| [Website](https://github.com/MRSIAVASSHIRBOT/MR_SIAVASH_AI) | `main` | `b0f878ff52d9bae4c59f2a862bacbb5642598e80` | 8/8 success | None |
| [EL_AI](https://github.com/MRSIAVASSHIRBOT/MR_SIAVASH_EL_AI) | `main` | `c4f3cf44d63ecf99db9a0b9dd82b0591a12de8ba` | 4/4 success | None |

All three repositories report `main.protected=false`. No access-control or branch-protection setting was changed.

## 2. Recent implementation evidence

- **Hub:** X8 publishes the shared security-boundary model; X7 propagates Bridge job correlation IDs; H15 adds authenticated, bounded process-local request metrics. PR [#631](https://github.com/MRSIAVASSHIRBOT/MR_SIAVASH_HUB_AI/pull/631) was merged as `5d3813d974a8e2681d47dea176f04f20546ffb4f`. Its exact PR head passed 13/13 checks. Post-merge main had 9/14 checks complete successfully and 5 still running at capture; do not call it fully green yet.
- **Website:** A12 consented, identifier-free aggregate analytics and A13 deterministic first-party static-payload budgets are merged. The current main has 8/8 observed checks successful, including Pages deployment and production/preview smoke. A12 and A13 remain partial; A14 is not complete.
- **EL_AI:** E12 bounds task-plan dispatch retries and burst size; X7 carries task-local Bridge correlation; X8 pins and tests the shared Hub security contract. Current main has 4/4 observed checks successful across Python 3.11/3.12, Workflow Security Audit and Scorecard.

## 3. Open PRs and deployment dependency

Hub [PR #618](https://github.com/MRSIAVASSHIRBOT/MR_SIAVASH_HUB_AI/pull/618) remains Draft and GitHub reports it is not mergeable. It proposes separate paid Hub and EL_AI Render services/disks; no resources were created. Explicit owner review of the complete recurring cost and Render Workspace Blueprint Auto Sync setting is required before any provisioning or Manual Sync.

The PR pins EL_AI at `57b8289fddbc53c4e7ee1709066939467770f3a0`. Current EL_AI main `c4f3cf44d63ecf99db9a0b9dd82b0591a12de8ba` is 10 commits ahead. The proposed worker therefore uses an older source revision; re-pin and rerun exact-head CI before any future approval. Website and EL_AI have no open PRs at capture.

## 4. Cross-repository contract and security boundaries

Hub's canonical `contracts/hybrid/v1/manifest.json` and EL_AI's pinned `tests/fixtures/hub-hybrid-manifest-v1.json` have the same Git blob SHA:

`e6c7ab219ccf0d9fd0613d09f8c12b2c8d1fdcdd`

The standardized X8 security model preserves:
- Hub pairing as authority for owner, Telegram identity and paired-client binding.
- `status:read` as the default paired scope; Hub-side route scope checks and PolicyEngine decisions remain authoritative.
- Deny for prohibited risk and missing required grants; explicit approval for high-risk or external side effects.
- Request/correlation IDs as diagnostic-only metadata, never authorization, ownership or idempotency signals.
- Website Gateway as public-only, with no private Hub calls, Hub credentials or owner context; browser code cannot hold Hub credentials.
- EL_AI credential storage through trusted OS keyring or explicitly keyed AES-256-GCM encrypted-file mode, with no plaintext fallback.

X7 uses a task-local canonical Bridge job UUID as `X-Correlation-ID`, distinct from each HTTP `X-Request-ID`. It must not contain Telegram identities, message text or lease tokens. Hub owns private state, policy, approvals and audit; EL_AI owns Telegram conversation/identity and scoped Hub-client behavior; Website owns public content and public Gateway. No shared database or ambient trust is introduced.

## 5. Dependencies, tests and workflows

| Repository | Runtime/dependencies | Quality gates |
|---|---|---|
| Hub | Python 3.11+, FastAPI, Pydantic, HTTPX, cryptography, SQLite | Backend CI, Python 3.11/3.12, Windows wheel/device checks, Linux/Windows FFmpeg, security scan, Workflow Security Audit, Scorecard |
| Website | Static application; Node 22 Gateway test package | Repository tests, Gateway CI, syntax/structure, Pages artifact/deployment, production/preview smoke, Workflow Security Audit, Scorecard |
| EL_AI | Python 3.11+, HTTPX, Keyring, python-dotenv, cryptography | Python 3.11/3.12 tests and Ruff, Telegram edge tests/workflow, Workflow Security Audit, Scorecard |

No Python lockfile was present in the inspected root manifests. Reproducible dependency locking remains follow-up work.

## 6. Live-service evidence boundary

The latest recorded Cloudflare/D1 read-only inspection in Website `docs/RELEASE_STATUS.md` is dated **2026-11-23 15:48 UTC**, not 2026-11-25. It reported Production Worker v14, Preview Worker v10, Preview query-string redaction disabled, and migrations 0001–0005 in the then-observed Production/Preview ledgers. Those values are historical and must be re-read before any release. GitHub runtime smoke does not prove that the latest source is deployed.

No Worker deployment, D1 migration, Render provisioning, secret/Access/DNS change, Telegram webhook registration, production Telegram message or device operation was performed in this checkpoint. Green CI is not live end-to-end acceptance.

## 7. Prioritized next steps

1. Finish the 5 outstanding post-merge Hub checks on `5d3813d`; inspect failures before further integration.
2. Keep Render PR #618 Draft; refresh the EL_AI pin to a reviewed current commit and rerun CI, but do not provision without explicit owner cost and Auto Sync approval.
3. Re-read Cloudflare Preview/Production configuration and D1 ledgers; verify Preview query-string redaction, exact source provenance, backups and rollback before any deployment.
4. Complete isolated Hub–EL_AI pairing, scoped authorization, expiry/revocation, Bridge pull/complete, idempotency and sanitized-failure acceptance. Do not send production messages.
5. Add Python dependency locks and have the owner/admin enforce required current-head checks on protected branches.
