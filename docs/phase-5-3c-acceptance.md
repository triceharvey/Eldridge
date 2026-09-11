# Phase 5.3C Evaluation Campaign and Read-Model Acceptance

## Outcome

Eldridge's evaluation domain is now separated from the transitional application façade. Campaign
policy and decisions live in `EvaluationCampaignService`; provider execution and trusted assessment
live in `EvaluationPipelineService`; repair, recovery, and promotion live in
`EvaluationLifecycleService`; and every evaluation response shape is produced by shared read-model
functions.

## Verified controls

| Control | Evidence |
|---|---|
| Stable external contract | Existing façade signatures, API routes, MCP callers, and response shapes remain unchanged |
| Deterministic campaign decisions | Existing candidate bounds, digest checks, independent-review policy, iteration limits, and winner/refinement rules remain intact |
| One read-model implementation | Thirteen policy/serialization functions replace façade-owned and duplicated component serializers |
| No reverse façade import | Campaign, pipeline, lifecycle, and read-model modules do not import `ControlPlaneService` |
| Existing security posture | Human capability checks, routing and egress policy, USD 0 ceiling, idempotency, locking, and audit writes remain unchanged |
| Structural equivalence | Four campaign methods and thirteen serializers match their pre-extraction abstract syntax after mechanical boundary renaming |
| Regression seam | Exact delegation tests cover campaign, pipeline, and lifecycle façade commands; existing API/evaluation tests exercise the composed services |

## Evidence scope

This is internal modular-monolith acceptance. It does not activate a live provider or prove the final
real-model vertical slice. The evaluation domain decomposition is complete, while workflow/task,
Git/PR, deployment/recovery, and integration boundaries remain pre-production maintainability work.

```bash
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy src
.venv/bin/pip-audit
.venv/bin/pytest -m 'not postgres'
CONTROL_PLANE_TEST_DATABASE_URL='postgresql+psycopg://control_plane:control_plane@localhost:55432/control_plane' .venv/bin/pytest -m postgres
```
