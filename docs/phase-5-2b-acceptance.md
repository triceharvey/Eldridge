# Phase 5.2B Durable Evaluation Campaign Acceptance

## Outcome

Evaluation campaigns now preserve their policy, routing snapshot, iteration, cumulative budget,
candidate provenance, decisions, and audit history across service restarts. Creation and evidence
submission are explicit human-authorized commands with replay-safe idempotency.

## Verified controls

| Control | Evidence |
|---|---|
| Workflow-bound risk | Campaign risk is derived from the parent workflow and cannot be downgraded by the request |
| Human command authority | Agents and integrations cannot create campaigns or submit evidence through this bridge |
| Durable state | Campaign, batch, and candidate rows retain decisions across service sessions |
| Replay safety | Actor-scoped idempotency and canonical request digests return the original batch or reject drift |
| Serialized iteration | The campaign row is locked; one batch is permitted per campaign iteration |
| Server-owned budget | Prior cost comes from durable campaign state and each decision replaces the cumulative total |
| Policy eligibility | Candidate and reviewer identities are rechecked for health, capability, egress, classification, risk, and evidence |
| Exact provenance | Provider family, model version, profile version, prompt contract, variant, and SHA-256 evidence are retained; every check and review binds the assessed output digest |
| Controller scoring | The HTTP boundary accepts no routing score; the current versioned router supplies it |
| Terminal containment | Winning and exhausted campaigns refuse later batches; refinement advances by one bounded iteration |
| No authority escalation | A winner cannot merge, deploy, publish, execute output, or replace a human approval |

## Current boundary

This slice accepts evidence assembled under an authenticated human operator and verifies its structure
and provider identity; it does not yet run the providers or trusted validators that produced that
evidence. Candidate content is represented only by a digest. The next slice must persist execution
intent before provider contact, run policy-eligible fan-out outside the transaction, contain ambiguous
outcomes, store revision-bound output artifacts, and feed trusted validation evidence back into this
campaign boundary.
