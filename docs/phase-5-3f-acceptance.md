# Phase 5.3F Controlled Deployment and Recovery Boundary Acceptance

## Outcome

Environment registration, immutable planning, dry-run execution, local credentialed deployment,
verification, controlled rollback, and recovery records now run behind a composed
`DeploymentRecoveryService`. The application facade keeps its external contract while credential and
recovery responsibilities have a focused internal boundary.

## Verified controls

| Control | Evidence |
|---|---|
| Stable external contract | Existing facade signatures, API routes, and response shapes remain unchanged |
| Default denial | Production environments remain prohibited and local credentialed execution still requires explicit activation |
| Immutable authorization | Environment digest, plan digest, merge revision, adapter, approval, and recovery attempt bindings remain exact |
| Credential containment | Raw credentials exist only inside broker callbacks; durable output contains redacted handles and references |
| Single-use approvals | Deployment and rollback approvals retain expiration checks and are consumed before target contact |
| Outcome containment | Unknown deployment outcomes and failed verification retain `UNKNOWN` or `ROLLBACK_REQUIRED` handling |
| Canonical state machine | Deployment and recovery transitions use the workflow component's transition operation |
| No reverse facade import | `deployment_recovery.py` does not import `ControlPlaneService` |
| Structural equivalence | All 22 extracted command, failure, query, conversion, and serialization bodies match their pre-extraction abstract syntax |
| Regression seam | Exact delegation coverage plus deployment, credentials, Kubernetes, API, permissions, observability, and PostgreSQL tests exercise the boundary |

## Evidence scope

This is an internal modular-monolith refactor. It does not enable local deployment, contact a live
cluster, run the destructive k3d exercise, provision hosted infrastructure, or spend provider/cloud
funds. The USD 0 default and separate approximately USD 5 future hosted exercise authorization remain
unchanged. Windsurf integration decomposition and the authorized real-model vertical slice remain.

```bash
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy src
.venv/bin/pip-audit
.venv/bin/pytest -m 'not postgres'
CONTROL_PLANE_TEST_DATABASE_URL='postgresql+psycopg://control_plane:control_plane@localhost:55432/control_plane' .venv/bin/pytest -m postgres
```
