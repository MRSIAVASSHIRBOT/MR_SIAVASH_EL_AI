# EL_AI Request Correlation Implementation Plan

> **For agentic workers:** Use the native inline execution approach; follow the test-driven-development cycle task-by-task.

**Goal:** Generate and propagate canonical request IDs for every EL_AI-to-Hub logical operation.

**Architecture:** Keep correlation inside the existing scoped HubClient. Pairing/refresh calls get a fresh UUID. Each authorized operation gets one UUID shared by the original request and any 401 retry. Emit structured logs with only request ID, status, and outcome.

**Tech Stack:** Python 3.11+, httpx, pytest.

**Spec:** [Hub Hybrid Request Correlation v1 design](https://github.com/MRSIAVASSHIRBOT/MR_SIAVASH_HUB_AI/blob/main/docs/superpowers/specs/2026-11-15-hybrid-request-correlation-v1-design.md)

## Global Constraints

- Do not change Telegram command authorization, Hub scopes, credential storage, or public Gateway routing.
- Never log credentials, request/response bodies, Telegram IDs, raw URLs, or query strings.
- Use one canonical UUID per logical Hub operation; reuse it for 401 retry.
- Pairing and refresh are independent operations and receive fresh IDs.

## Review Focus

- A 401 retry reuses the original request ID while the refresh call gets a distinct ID.
- Pairing failures do not retain pairing codes in exception context.
- Authenticated failures do not retain bearer credentials in exception context.
- Missing/invalid server response headers do not leak response details or break callers.
- Concurrent requests receive independent IDs.

---

### Task 1: Scoped HubClient propagation

**Files:**
- Modify: `src/el_ai/integrations/hub.py`
- Test: `tests/test_hub_client.py`
- Modify: `docs/operations/observability.md`

**Interfaces:**
- Produces: `HubClientError.request_id: str | None`.
- Consumes: no caller-provided IDs; HubClient generates UUIDs internally.

- [ ] Add tests asserting pairing and authenticated requests carry valid UUID headers.
- [ ] Add a 401 retry test asserting the original operation reuses its ID and refresh receives a separate ID.
- [ ] Run targeted tests and confirm expected failure before implementation.
- [ ] Add request ID generation, header propagation, sanitized request ID on HubClientError, and safe structured completion/failure logs.
- [ ] Document privacy-safe request correlation.
- [ ] Run targeted tests, full pytest, Ruff, and workflow security audit.
- [ ] Commit and open a reviewable PR; do not merge automatically.

### Task 2: Cross-service verification

- [ ] Verify Hub and EL_AI contract wording agrees on UUID validation, replacement, echo, and log redaction.
- [ ] Confirm exact branch SHAs have green CI and security workflows.
- [ ] Review diffs for secret, identity, URL, or payload logging.
