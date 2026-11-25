# E12 — Owner-wide Telegram plan pause switch

Status: implemented for future EL_AI task-plan dispatch; already-submitted Hub tasks remain under Hub control.

## Commands

- `/taskplanspauseall CONFIRM` atomically marks every stored plan belonging to the authenticated Telegram owner as paused.
- `/taskplansresumeall CONFIRM` atomically clears that owner's plan pause state.

Both commands require the exact `CONFIRM` argument. The command path is local to the owner-scoped plan store and remains available when the Hub integration is temporarily unavailable.

## Operational semantics

The EL_AI plan orchestrator already checks the persisted `paused` flag before dispatching each new step. A pause-all operation therefore prevents future plan steps from being submitted by this EL_AI instance. A resume-all operation only re-enables progression; the next normal orchestration cycle decides which dependency-ready step can be submitted.

**This is not a remote task kill operation.** Tasks that were already submitted to Hub continue under Hub's worker, approval, cancellation and emergency-stop policies. The pause switch does not claim to stop active inference, FFmpeg work, or any task already executing. Use the existing per-plan cancellation command for eligible queued/approval-waiting tasks, or the Hub control plane for its separately authorized worker stop.

## Dispatch burst budget

Each normal advancement cycle may attempt at most **3 Hub task-creation requests across all owners and plans**. The counter is global to that invocation, not reset for each owner or plan. A failed, rejected, or timed-out creation attempt consumes one slot; this prevents transient failures from producing an unbounded request burst. Remaining eligible steps are left unsubmitted for a later normal cycle.

This is a per-cycle burst guard only. It is **not** a daily/monthly usage budget, billing cap, global concurrency limit, or guarantee that only three tasks can be active. Hub authorization, approval, idempotency, rate limits, provider budgets, and its own execution controls remain authoritative.

## Safety

- State changes are restricted to plans owned by the authenticated Telegram user.
- All changed plans are written in one atomic local state replacement.
- Repeated pause/resume requests are idempotent and report the number of records changed.
- No task submission, Hub request, cancellation, or provider call occurs while toggling the local switch.
- Missing confirmation, malformed state, or persistence errors fail without dispatching work.

## Verification

Regression tests cover owner isolation, atomic update semantics, idempotent repeated operations, confirmation requirements, Bridge command allowlist parity and operation while the Hub client is unavailable.
