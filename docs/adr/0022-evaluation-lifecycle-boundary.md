# ADR-0022: Extract the Evaluation Lifecycle Boundary

Status: Accepted and implemented on 2026-09-10 as the first Phase 5.3 maintainability slice.

## Context

`ControlPlaneService` is the stable application façade used by the API, MCP boundary, and tests, but
it has accumulated unrelated orchestration responsibilities. Repair planning, interrupted-assessment
recovery, and winner promotion are security-sensitive evaluation lifecycle transactions with a
cohesive policy and persistence boundary. Leaving their implementations inside the façade increases
review cost and makes later evaluation changes harder to isolate.

## Decision

- Preserve the public `ControlPlaneService` command signatures and route them to a composed
  `EvaluationLifecycleService`.
- Move the complete repair, recovery, and promotion transactions—including authorization,
  idempotency, locking, audit events, and conservative failure handling—into that component.
- Inject only the two assessment operations needed to resume or read an assessment. Late-bound
  callbacks preserve test substitution and avoid a reverse import from the lifecycle component to
  the façade.
- Keep the component in the modular monolith with the same database and policy engine. This is not a
  network service and adds no deployment dependency.
- Retain shared evaluation read-model serializers in the façade temporarily; move them only when a
  broader evaluation query boundary can replace them without duplication.

## Consequences

The façade loses hundreds of lines of transactional orchestration while callers observe the same API
and policy behavior. Focused behavior tests still exercise the real component through the façade, and
a delegation test protects the seam. Workflow, evaluation execution/assessment, deployment, and
integration responsibilities remain to be extracted in later bounded slices, so this ADR does not
claim that the overall decomposition is complete.
