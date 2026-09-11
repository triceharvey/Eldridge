# ADR-0023: Extract the Evaluation Execution and Assessment Pipeline

Status: Accepted and implemented on 2026-09-11 as the second Phase 5.3 maintainability slice.

## Context

Phase 5.3A separated human-controlled repair, recovery, and promotion, but committed provider
fan-out and trusted assessment still occupied more than 1,500 lines of `ControlPlaneService`.
Execution preparation, provider dispatch, ambiguity reconciliation, deterministic validation,
independent review, and evidence submission share one workflow snapshot and one conservative
failure model. Splitting those stages across unrelated owners would weaken that chain of evidence.

## Decision

- Compose an `EvaluationPipelineService` behind the existing `ControlPlaneService` API.
- Move execution planning and fan-out, execution reconciliation, trusted artifact construction,
  deterministic checks, independent review, review reconciliation, provider observation, and final
  assessment submission into that component.
- Inject the policy engine, routing inputs, provider bindings, validators, evidence store, and narrow
  callbacks needed for campaign decision submission and shared read-model serialization.
- Describe provider bindings with a structural protocol. The pipeline does not import the façade,
  including at runtime or for type checking.
- Retain late-bound façade callbacks where interruption tests and lifecycle recovery deliberately
  substitute assessment continuation behavior.
- Keep the boundary in-process with the existing database. It introduces no queue, network service,
  provider activation, cost, or new authority.

## Consequences

The stable façade loses more than 1,500 lines of security-sensitive orchestration while preserving
its caller contract. The complete output-to-decision evidence chain now has a dedicated owner and can
be reviewed independently from Git, deployment, and worker operations. Campaign creation, manual
evidence submission, and shared evaluation read models remain in the façade for a later bounded
slice; the overall service decomposition is therefore not complete.
