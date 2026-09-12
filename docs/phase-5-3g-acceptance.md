# Phase 5.3G Windsurf Integration Boundary Acceptance

## Outcome

Windsurf task claims, scoped leases, heartbeats, handoff artifacts, and Git-verified evidence now run
behind a composed `WindsurfIntegrationService`. The application facade and MCP tools keep their
existing contracts while the last primary integration responsibility has a focused internal
boundary.

## Verified controls

| Control | Evidence |
|---|---|
| Stable external contract | Existing facade signatures and MCP tool schemas remain unchanged |
| Explicit activation | Repository handoff and the MCP server remain disabled unless separately configured |
| Scoped identity | Claims authorize a named principal and create only task-scoped heartbeat and evidence grants |
| Expiring authority | Claims retain bounded leases; heartbeat and submission validate owner, token, status, and expiration |
| Immutable handoff | Repository, base revision, branch, objective, principal, policy version, and writable paths remain digest-bound |
| Git-derived evidence | The repository registry verifies the branch, base, result revision, and actual changed paths |
| Honest test evidence | IDE-reported test status remains marked non-authoritative |
| Canonical workflow rules | Lease validation, grant deactivation, and task advancement reuse `WorkflowTaskService` |
| No reverse facade import | `windsurf_integration.py` does not import `ControlPlaneService` |
| Regression seam | Exact delegation coverage plus handoff, MCP, repository, lease, and PostgreSQL tests exercise the boundary |

## Evidence scope

This is an internal modular-monolith refactor. It does not enable Windsurf MCP, invoke a paid model,
send repository data to a provider, create a real model-authored pull request, or change the USD 0
default. Phase 5.3's primary domain decomposition is complete. The next evidence gate is one
explicitly authorized real-model vertical slice through deterministic validation, independent
review, protected pull request, and human disposition.

```bash
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy src
.venv/bin/pip-audit
.venv/bin/pytest -m 'not postgres'
CONTROL_PLANE_TEST_DATABASE_URL='postgresql+psycopg://control_plane:control_plane@localhost:55432/control_plane' .venv/bin/pytest -m postgres
```
