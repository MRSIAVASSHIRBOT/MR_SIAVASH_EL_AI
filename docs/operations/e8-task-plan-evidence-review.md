# E8 — Task-plan evidence review

Status: implemented as a read-only deterministic evidence check; semantic quality evaluation and automatic correction remain out of scope.

## Command

`/taskplanevaluate PLAN_ID` is an owner-only command available through both the local polling runtime and the Cloudflare Bridge allowlist. The plan lookup is scoped to the exact paired Telegram user before any Hub read occurs.

## Evidence contract

For each submitted plan step, EL_AI reads the owner-scoped Hub task status. It requests a worker result only when the task status is `succeeded`. A result is counted as available only when the Hub envelope contains a non-empty object result, no error code, a bounded positive attempt count, and an ISO-8601 finish time with an explicit timezone.

Task outcomes are classified as:

- `available`: successful task with a structurally valid terminal result envelope.
- `missing`: task succeeded, but its result is unavailable or structurally invalid.
- `failed`: task reached failed, cancelled, or rejected status.
- `pending`: task is queued, running, or waiting for approval.
- `blocked`: the local plan has a durable blocked checkpoint.
- `not_submitted`: the owner has not submitted this plan step.
- `unknown`: task/result retrieval failed or returned an unsupported status.

Plan summaries are `evidence_complete`, `needs_attention`, `insufficient_evidence`, `unknown`, or `in_progress`. Every report explicitly identifies its scope as `tool_execution_only`, returns `goal_satisfaction: not_evaluated`, and marks `acceptance_criteria_evaluation: not_implemented`. A successful task status alone is never treated as result evidence.

## Safety and limits

- Reads use the existing paired-client `tasks:read` and `tasks:result` scopes.
- The evaluator does not return result payloads, task descriptions, private context, or credentials.
- It never creates, retries, cancels, resumes, or modifies a task or plan.
- Each evaluation is a point-in-time snapshot; it is not persisted as an authoritative verdict.
- A structurally valid result envelope proves that a result is available, not that its semantic content satisfies a goal. The report must never be presented as goal completion or acceptance-criteria success. Semantic evaluation against explicit, versioned acceptance criteria and independently verifiable evidence remains a future E8 capability.
- Unknown or missing evidence must be reviewed; it must not trigger automatic retries or self-modification.

## Verification

Regression tests cover valid results, missing results, malformed envelopes, failed/blocked steps, pending steps, unavailable Hub status, owner isolation, Bridge command parity, and the guarantee that the evaluation path performs no external mutations.

Live Telegram/Hub runtime acceptance remains a separate release gate.
