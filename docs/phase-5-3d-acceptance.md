# Phase 5.3D Workflow and Task Boundary Acceptance

## Outcome

Eldridge's workflow and task lifecycle now runs behind a composed `WorkflowTaskService` while
`ControlPlaneService` remains the stable application facade. The component owns intake, task
leasing, lease heartbeat, provider routing and execution, retries, reconciliation, human input
disposition, scoped approvals, and cancellation.

## Verified controls

| Control | Evidence |
|---|---|
| Stable external contract | Existing facade signatures, API routes, MCP callers, and response shapes remain unchanged |
| Transactional workflow behavior | Existing transactions, row locks, state-machine checks, task grants, audit events, and idempotency remain intact |
| Bounded execution | Existing provider eligibility, egress constraints, lease lifetime, heartbeat, validation, retry, and reconciliation behavior remains intact |
| Human authority | Input disposition, execution reconciliation, merge approval, deployment approval, and rollback approval retain human-only capability checks |
| No reverse facade import | `workflow_tasks.py` uses a structural provider-binding protocol and does not import `ControlPlaneService` |
| Structural equivalence | All 30 moved command and helper bodies match their pre-extraction abstract syntax |
| Regression seam | Exact delegation coverage plus workflow, lease, execution, routing, policy, API, and Windsurf regression tests exercise the composed service |

## Evidence scope

This is an internal modular-monolith refactor. It does not activate a paid model, relax the USD 0
default, deploy hosted infrastructure, or prove the final real-model vertical slice. Shared facade
helpers remain temporarily for the Git/PR, deployment/recovery, and Windsurf paths; those domains
remain the next maintainability boundaries.

```bash
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy src
.venv/bin/pip-audit
.venv/bin/pytest -m 'not postgres'
CONTROL_PLANE_TEST_DATABASE_URL='postgresql+psycopg://control_plane:control_plane@localhost:55432/control_plane' .venv/bin/pytest -m postgres
```
