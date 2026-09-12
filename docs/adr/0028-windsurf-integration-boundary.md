# ADR-0028: Extract the Windsurf integration boundary

- Status: Accepted
- Date: 2026-09-11

## Context

`ControlPlaneService` still implemented the complete Windsurf lifecycle: repository-bound task
claims, scoped capability grants, lease heartbeats, handoff serialization, Git revision evidence,
and workflow advancement. These responsibilities are integration-specific and were the last primary
domain left inside the application facade after evaluation, workflow, Git, and deployment were
separated.

The boundary must not weaken the existing controls. Windsurf remains disabled unless a repository
registry and MCP activation are explicitly configured. A claimed task must remain an implementation
task in the correct workflow state, bound to one principal, repository scope, base revision, branch,
policy version, writable-path set, expiring lease, and handoff digest.

## Decision

Create `WindsurfIntegrationService` as an in-process component owning claim, heartbeat, evidence
submission, and handoff serialization. `ControlPlaneService` retains its public signatures and
delegates to the component, so API and MCP contracts remain stable.

The component receives the policy engine, repository registry, configured lease lifetime, and the
canonical `WorkflowTaskService`. It reuses the workflow service's lease validation, task-grant
deactivation, task serialization, and state advancement instead of creating competing workflow
rules. It never imports the facade.

Submitted evidence remains non-authoritative until Git verifies the exact branch, base, result
revision, and changed paths. A self-reported test result remains explicitly labeled as a claim.

## Alternatives considered

- Keep Windsurf logic in the facade. Rejected because integration details obscure the stable
  application contract and make future IDE adapters harder to review independently.
- Create a standalone MCP network service with its own persistence logic. Rejected because it would
  duplicate transactions and trust rules without a current scale or isolation requirement.
- Generalize every IDE before extracting Windsurf. Rejected because no second implementation has
  supplied evidence for a safe common abstraction.

## Consequences

- The primary Phase 5.3 domain decomposition is complete.
- Windsurf's privilege and evidence boundary is independently reviewable and testable.
- The external facade, MCP protocol, disabled-by-default posture, and USD 0 baseline do not change.
- Small shared query and compatibility helpers may remain in the facade; they no longer own a
  primary orchestration domain.

## Failure behavior

Unconfigured repositories, unauthorized principals, non-implementation tasks, stale or mismatched
leases, changed workflows, invalid handoff digests, unregistered branches, and mismatched Git file
evidence fail closed. Replayed identical evidence remains idempotent; conflicting evidence is
rejected. No failure grants merge or deployment authority.
