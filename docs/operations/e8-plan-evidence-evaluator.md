## E8 — Plan Evidence Evaluator

### Scope
This change introduces a deterministic, read-only evaluator for Telegram task plans. It distinguishes terminal task status from verified result-envelope evidence and reports uncertain or missing evidence without retrying or modifying tasks.

### Contract
- `evidence_complete` is emitted only when all plan steps have valid result envelopes.
- `insufficient_evidence`, `unknown`, `in_progress`, and `needs_attention` are distinct outcomes.
- Result payload contents are not retained in the report; only bounded status, attempt count, and completion timestamp are exposed.
- The evaluator does not perform semantic quality evaluation or automatic correction/replanning.
- Reads use the existing owner-scoped Hub client. Parallel reads are bounded.

### Test coverage
Tests cover verified results, missing result evidence, failed/blocked steps, pending/unsubmitted steps, unknown task status, malformed envelopes, and payload privacy.

### Rollback
Revert this branch/PR; the evaluator is additive and not wired into autonomous task mutation.