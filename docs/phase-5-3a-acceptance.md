# Phase 5.3A Evaluation Lifecycle Boundary Acceptance

## Outcome

Eldridge has begun the pre-production service decomposition without changing its public application
API. Human-controlled evaluation repair, interrupted-assessment recovery, and exact-winner promotion
now execute through a dedicated `EvaluationLifecycleService` composed behind
`ControlPlaneService`.

## Verified controls

| Control | Evidence |
|---|---|
| Stable caller contract | The existing façade method names, arguments, return models, API routes, and MCP callers are unchanged |
| Cohesive transaction owner | Repair, recovery, and promotion authorization, locking, idempotency, persistence, and audit writes reside in one lifecycle component |
| No authority widening | The extracted component receives the existing policy engine and retains human-only capability checks |
| No new infrastructure | The boundary remains in-process and uses the existing session factory and database |
| Conservative recovery retained | Interrupted work is still failed or marked unknown according to dispatch state; ambiguous calls are not blindly retried |
| Testable seam | A façade delegation regression test verifies exact argument forwarding, while existing campaign and validation tests exercise behavior end to end |

## Evidence scope

Acceptance covers an internal modular-monolith refactor. It does not prove a real paid-provider run,
hosted infrastructure, or completion of the full service decomposition. The remaining pre-production
maintainability work is to extract the broader evaluation execution/assessment, workflow, deployment,
and integration boundaries in reviewable slices.

```bash
.venv/bin/ruff check .
.venv/bin/mypy src
.venv/bin/pytest -q tests/test_service_boundaries.py tests/test_evaluation_campaigns.py tests/test_evaluation_validation.py
.venv/bin/pytest -m 'not postgres'
CONTROL_PLANE_TEST_DATABASE_URL='postgresql+psycopg://control_plane:control_plane@localhost:55432/control_plane' .venv/bin/pytest -m postgres
```
