# Phase 5.3B Evaluation Pipeline Boundary Acceptance

## Outcome

Eldridge now runs committed model fan-out and trusted assessment through a dedicated
`EvaluationPipelineService`. `ControlPlaneService` remains the stable application façade, but no
longer owns the implementation of provider execution, ambiguity reconciliation, deterministic
validation, independent review, or assessment submission.

## Verified controls

| Control | Evidence |
|---|---|
| Stable external contract | Existing service method names, API routes, MCP callers, inputs, and return models are unchanged |
| Committed-before-contact execution | Provider and reviewer intent still commits before external invocation |
| Snapshot integrity | Execution, artifacts, checks, reviews, and final decisions remain bound to workflow version and candidate revision |
| Conservative ambiguity handling | Unknown provider or reviewer outcomes remain blocked pending explicit human reconciliation |
| Trusted evidence chain | Controller-owned validators and independent reviews remain digest-bound before campaign submission |
| Least coupling | Provider bindings use a structural protocol and the pipeline does not import the façade |
| No authority or cost expansion | Existing capability checks, local-egress requirement, and USD 0 execution ceiling remain unchanged |
| Regression seam | Delegation tests verify exact façade forwarding; evaluation behavior tests exercise the real composed pipeline |

## Evidence scope

This acceptance record covers an internal modular-monolith refactor, not a live commercial-provider
exercise. Campaign administration and shared evaluation read models still need a bounded owner, and
workflow, Git, deployment, and integration orchestration remain in the transitional façade.

```bash
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy src
.venv/bin/pip-audit
.venv/bin/pytest -m 'not postgres'
CONTROL_PLANE_TEST_DATABASE_URL='postgresql+psycopg://control_plane:control_plane@localhost:55432/control_plane' .venv/bin/pytest -m postgres
```
