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


## Durable per-step dispatch budget

In addition to the three-request global per-cycle burst guard, each unsubmitted plan step has a persistent maximum of **5 Hub task-creation attempts**. The counter is written before each external request, so process restarts do not reset the budget. If the Hub returns an unknown/transient failure, retries use the same deterministic idempotency key and a persisted exponential full-jitter delay (base 5 seconds, capped at 60 seconds). Policy/validation denials continue to block immediately. After the fifth unsuccessful attempt, the step is durably blocked with `dispatch_attempts_exhausted` and requires a new owner-reviewed plan rather than retrying forever.

The local plan-store schema is version 5. Existing v1-v4 plans migrate forward with zero dispatch attempts and no retry deadline; their task IDs, dependency graph, blocked checkpoints, pause state and versioned planning metadata are preserved. A controlled `TaskPlanStore.downgrade_to_v4()` path writes the compatible v4 representation before an application rollback; it discards only the new retry counters/deadlines. Pause dispatch advancement and create a private backup before using that method. An attempt reservation is saved before the Hub call; a crash between reservation and request conservatively consumes one attempt. The plan's existing 7-day retention is its maximum local lifetime.

This is a bounded dispatch-retry budget, not a daily/monthly provider billing cap, global active-task concurrency cap, or remote cancellation/kill operation. Already-submitted Hub tasks remain controlled by Hub policy and Worker controls.


A dispatch attempt also stores a short in-flight reconciliation deadline before the outbound Hub call. If EL_AI restarts or loses the response, the next cycle waits until that persisted deadline and then repeats only with the same deterministic idempotency key. A successful submission or terminal block clears the retry checkpoint. This does not cancel an already-created Hub task; Hub remains authoritative for any task that was accepted before the response was lost.


### Rollback procedure

1. Pause the owner-scoped plan scheduler and stop new local dispatch cycles.
2. Create a private mode-0600 backup of the active task-plan file.
3. With this version of EL_AI, load the store and call `TaskPlanStore.downgrade_to_v4()`; verify the resulting JSON has `version: 4`, retains plan/task IDs, dependencies and pause state, and contains no `dispatch_attempts` or `next_dispatch_at` fields.
4. Roll back the application code. The legacy v4 reader can then load the store. The retry-budget state is intentionally removed; do not resume automatic dispatch until the rollback's retry policy is understood.
