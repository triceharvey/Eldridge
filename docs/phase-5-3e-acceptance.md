# Phase 5.3E Git and Protected Pull-Request Boundary Acceptance

## Outcome

Eldridge's GitHub CI and protected pull-request lifecycle now runs behind a composed
`GitPullRequestService`. The stable application facade delegates CI evidence ingestion and queries,
draft proposal creation, unknown-outcome reconciliation, merge-readiness assessment, and
authoritative merge confirmation.

## Verified controls

| Control | Evidence |
|---|---|
| Stable external contract | Existing facade signatures, API routes, webhook behavior, and response shapes remain unchanged |
| No merge authority | The component contains no remote merge operation; it proposes drafts and confirms external protected merges |
| Exact-revision binding | Proposal, readiness, approval, and confirmation records must agree with the workflow candidate revision |
| Human gate | Merge confirmation still requires human capability and previously consumed exact-revision approval evidence |
| Ambiguous outcome containment | Pull-request creation failures become `UNKNOWN` and require read-only remote reconciliation |
| Canonical state machine | Successful confirmation uses the workflow component's transition operation to enter `MERGED` |
| No reverse facade import | `git_pull_requests.py` does not import `ControlPlaneService` |
| Structural equivalence | All 21 extracted command, failure, query, and serialization bodies match their pre-extraction abstract syntax |
| Regression seam | Exact delegation coverage plus Git operation, GitHub App, webhook, identity, API, permission, and workflow tests exercise the boundary |

## Evidence scope

This acceptance covers internal modularity and existing mocked GitHub contracts. It does not claim a
new live GitHub App exercise, perform a remote merge, activate paid providers, or relax the USD 0
default. The repository's own protected PR and CI path supplies release evidence for this change.
Deployment/recovery and integration decomposition remain.

```bash
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy src
.venv/bin/pip-audit
.venv/bin/pytest -m 'not postgres'
CONTROL_PLANE_TEST_DATABASE_URL='postgresql+psycopg://control_plane:control_plane@localhost:55432/control_plane' .venv/bin/pytest -m postgres
```
