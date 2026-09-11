# ADR-0024: Extract Evaluation Campaign and Read-Model Boundaries

Status: Accepted and implemented on 2026-09-11 as the third Phase 5.3 maintainability slice.

## Context

Phase 5.3B extracted execution and trusted assessment, but campaign creation, evidence decisions,
campaign queries, and every evaluation serializer still lived in `ControlPlaneService`. The pipeline
and lifecycle components consequently received façade-owned serialization callbacks, leaving a
reverse conceptual dependency and duplicating three lifecycle serializers.

## Decision

- Compose an `EvaluationCampaignService` behind the unchanged `ControlPlaneService` API.
- Move campaign creation, candidate-evidence submission, deterministic winner/refinement decisions,
  and campaign queries into that component.
- Centralize all evaluation policy and read-model serialization in read-only functions in
  `evaluation_read_models.py`.
- Make the campaign, pipeline, and lifecycle components consume those functions directly. Remove
  façade serializer callbacks and lifecycle serializer duplication.
- Use a structural provider-binding protocol so campaign administration does not import the façade.
- Retain the existing policy engine, transactions, routing constraints, USD 0 ceiling, audit events,
  and database. No provider, permission, service, or infrastructure is activated by this refactor.

## Consequences

All evaluation orchestration now has explicit campaign, pipeline, lifecycle, and read-model owners.
The transitional façade loses another several hundred lines while its external contract remains
stable. Shared serialization has one implementation and can evolve without a façade dependency.
Workflow/task, Git/PR, deployment/recovery, and integration orchestration remain to be extracted;
therefore this decision completes the evaluation decomposition, not the entire service decomposition.
