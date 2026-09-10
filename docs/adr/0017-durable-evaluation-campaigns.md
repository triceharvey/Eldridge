# ADR 0017: Durable, Policy-Bound Evaluation Campaigns

## Status

Accepted for implementation on 2026-09-10 as the second Phase 5.2 production slice.

## Context

The deterministic evaluator in ADR 0016 could replay a supplied batch but did not preserve campaign
state across process restarts. It also lacked an authenticated command boundary, cumulative budget
state, immutable batch history, and a durable record of the routing policy that made a provider
eligible. Connecting provider calls before establishing those controls would make retries, ambiguous
outcomes, and evidence provenance difficult to contain.

## Decision

Persist evaluation campaigns, batches, and candidate evidence in the control-plane database. Only an
authenticated human operator may create a campaign or submit externally assembled evidence in this
slice. The campaign derives its risk from the parent workflow, requires a declared work capability
and deterministic checks, and stores explicit fan-out, prompt-variant, iteration, review, and
cumulative-cost ceilings. High and critical risk automatically retain the two-review floor.

At evidence submission, the service locks campaign state, enforces one immutable batch per iteration,
and handles command replay with an actor-scoped idempotency key and request digest. It re-evaluates
provider and reviewer eligibility against current routing, egress, data-classification, risk, health,
and evidence policy. Provider family, model version, and profile version must exactly match the
configured policy identity. Routing scores are computed by the controller and replace any value in a
service-level submission; the HTTP contract does not accept a caller-supplied score.

Every decision stores the exact routing snapshot, candidate/check/review digests, explicit check and
review bindings to the candidate output digest, rejection reasons,
rank, cumulative cost, policy versions, and an audit event. A terminal campaign cannot accept another
batch, while a refinement decision advances only to the next bounded iteration.

## Consequences

- Campaign decisions survive restart and can be independently inspected and replayed.
- The database, not the client, controls iteration progression and cumulative cost.
- Arbitrary or stale provider and reviewer identities fail closed.
- Candidate output content is not accepted or executed; only its digest and evidence metadata are
  retained in this slice.
- A recorded winner remains an evaluation result and grants no workflow, merge, deployment, or
  publication authority.
- Trusted validator execution, provider fan-out, revision-bound output storage, retry scheduling, and
  artifact promotion remain later slices.

## Alternatives

- **Keep campaign state in process memory:** rejected because restarts would lose cost and iteration
  boundaries and could repeat paid work.
- **Let clients submit ranking scores:** rejected because that would turn a controller-owned policy
  decision into untrusted evidence.
- **Call providers inside the database transaction:** rejected because a timeout or ambiguous remote
  write would hold locks and blur durable intent with external execution.
- **Treat human-submitted check metadata as final production validation:** rejected; this intake path
  is an inspectable bridge until trusted validators record evidence directly.
