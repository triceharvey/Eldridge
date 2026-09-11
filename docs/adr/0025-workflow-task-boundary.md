# ADR-0025: Extract the workflow and task orchestration boundary

- Status: Accepted
- Date: 2026-09-11

## Context

`ControlPlaneService` remained responsible for workflow intake, task leasing, provider execution,
retry and reconciliation, and human decisions after the evaluation domain was extracted. Those
commands form one transactional lifecycle, but keeping their implementations in the application
facade made the facade harder to review and obscured the boundary between orchestration and API
compatibility.

Several helper operations are also used by Windsurf, Git/PR, and deployment flows. Moving all of
them at once would force unrelated domain changes or create circular imports.

## Decision

Create `WorkflowTaskService` as an in-process component that owns workflow creation and reads, task
leasing and heartbeat, provider routing and execution, retry handling, execution reconciliation,
input disposition, scoped approvals, and cancellation. `ControlPlaneService` keeps the existing
public signatures and delegates those commands to the component.

The extracted implementation retains the existing policy engine, database transactions, row locks,
lease lifetimes, provider eligibility rules, audit events, retry limits, and human-only decision
checks. Provider bindings are accepted through a structural protocol so the component does not
import the facade. Shared helpers used by the remaining domains stay as compatibility shims until
those domains receive their own boundaries.

## Alternatives considered

- Split workflow, scheduler, execution, and approval into separate network services. Rejected
  because it adds distributed failure modes without measured scale evidence.
- Move every shared helper immediately. Rejected because it would couple this refactor to Windsurf,
  Git/PR, and deployment behavior.
- Leave the implementations in the facade. Rejected because it preserves the reviewability problem
  identified by the external static assessment.

## Consequences

- Workflow and task commands can be tested and evolved behind a focused boundary.
- API and MCP callers keep their existing contract.
- No provider, credential, egress, budget, or deployment authority is added.
- Some temporary compatibility helpers remain in the facade; Git/PR, deployment/recovery, and
  integration decomposition are still required.

## Failure behavior

Construction fails under the same invalid lease and provider configuration conditions as before.
Runtime failures continue to follow the existing fail-closed routing, bounded retry, lease expiry,
reconciliation, and audit behavior. Delegation does not catch or reinterpret domain exceptions.
