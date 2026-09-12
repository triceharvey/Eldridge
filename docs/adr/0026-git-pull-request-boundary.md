# ADR-0026: Extract the Git and protected pull-request boundary

- Status: Accepted
- Date: 2026-09-11

## Context

After workflow/task extraction, `ControlPlaneService` still implemented GitHub CI evidence ingestion,
draft pull-request proposals, ambiguous-operation reconciliation, merge-readiness assessment, and
authoritative merge confirmation. These operations share one security boundary: GitHub is external,
side effects may have unknown outcomes, and every decision must remain bound to the exact candidate
revision and protected-branch policy.

Keeping that implementation in the application facade made the external-operation lifecycle harder
to audit independently and left Git-specific persistence mixed with deployment orchestration.

## Decision

Create `GitPullRequestService` as an in-process component owning CI evidence, pull-request proposals,
read-only reconciliation, readiness assessment, and merge confirmation. `ControlPlaneService` keeps
its public signatures and delegates to the component.

The component receives the configured GitHub App client and the canonical workflow transition
operation. It does not import the facade and cannot create a separate state-transition path. Existing
authorization, idempotency, revision matching, human-only merge confirmation, consumed approval,
unknown-outcome handling, and audit semantics remain unchanged.

## Alternatives considered

- Create a separate Git network service. Rejected because no measured scale or isolation requirement
  justifies another deployment boundary.
- Let the component update workflow state directly. Rejected because it would duplicate the
  state-machine authority already owned by the workflow component.
- Combine Git and deployment extraction. Rejected because protected-PR evidence and deployment
  credentials have different trust and failure boundaries.

## Consequences

- The complete protected-PR lifecycle is independently reviewable and testable.
- API and webhook contracts remain stable.
- GitHub integration remains disabled unless explicitly configured.
- No merge endpoint or autonomous merge authority is introduced; Eldridge only proposes, assesses,
  reconciles, and independently confirms external state.
- Deployment/recovery and Windsurf integration remain separate extraction work.

## Failure behavior

Disabled integration fails closed. Ambiguous pull-request creation is recorded as `UNKNOWN` and
requires read-only reconciliation. Failed readiness and confirmation attempts are recorded and
audited. Stale revisions, missing consumed approval, unprotected evidence, and mismatched repository
state remain blocking conflicts.
