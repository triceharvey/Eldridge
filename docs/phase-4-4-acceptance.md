# Phase 4.4 Acceptance Record

## Scope

Phase 4.4 is complete for the approved local non-production recovery slice as of 2026-09-09. The
exercise deliberately corrupted the release marker after a confirmed deployment update, proved
containment in `ROLLBACK_REQUIRED`, consumed a separate exact rollback approval, restored the
recorded pre-deployment snapshot, independently verified it, and reached `ROLLED_BACK`.

This closes the local Phase 4 controlled-deployment path. It does not authorize production,
hosted infrastructure, `tofu apply`, a full Eldridge application release, or rollback of database
or irreversible changes.

## Implemented controls

| Boundary | Evidence |
|---|---|
| Separate authority | `ROLLBACK` is distinct from `DEPLOY`, with separate approval and execution capabilities |
| Exact approval | Approval binds workflow, failed attempt, environment, plan digest, merged revision, policy version, rollback reference, approver, rationale, and expiry |
| Durable intent | A unique `RUNNING` rollback record and consumed approval commit before credential issuance or target contact |
| Exact credential | A fresh 600-second token is bound to the exact plan, audience, subject, resource, and `ROLLBACK` operation |
| Recorded target only | Recovery accepts only the pre-deployment release-marker snapshot stored by the failed deployment; arbitrary fields and malformed bindings fail closed |
| Independent verification | Recovery succeeds only after a separate Kubernetes read exactly matches the complete recorded snapshot |
| Idempotency | A successful replay returns the durable result without another broker request or target update |
| Ambiguous outcome | Timeout or transport loss during rollback becomes non-replayable `UNKNOWN`; the workflow remains `ROLLBACK_REQUIRED` |
| Rejected recovery | A rejected rollback decision is consumed and audited without releasing containment |
| Audit and timing | Approval, intent, operation, observation, verification, transition, errors, and recovery duration are retained without token material |
| Schema safety | PostgreSQL upgrade, schema-drift check, downgrade to 0009, re-upgrade to 0010, and a second drift check passed |
| Teardown and cost | No k3d cluster or matching container remained; no cloud or paid resource was used, preserving the USD 0 ceiling |

## Live evidence

The final exercise ran against implementation commit
`52cf6ee4b34b3a5b8589babbae1f640bd33866b6`. The artifact remained the identity-boundary
manifest at
`sha256:3feafc42cf6ad60dfaeae88a25c75ee5bcd960562f9321d0a7fa246f0e5267ba`; the controlled
recovery plan digest was
`da0e9804987f3aa8bb077899d029147eefde627783e5f5411dd840d5e202d03d`.

Sanitized evidence reported:

- `fault_injected=true`;
- failed attempt and workflow state `ROLLBACK_REQUIRED`;
- `rollback_approval_consumed=true` and credential operation `ROLLBACK`;
- real, non-simulated rollback with `changed=true`;
- `rollback_verified=true`, rollback status `SUCCEEDED`, and final state `ROLLED_BACK`;
- valid end-to-end audit chain;
- measured rollback execution duration of 45 ms; and
- four intended RBAC permissions allowed and eight widening attempts denied.

The complete non-live suite passed with 204 tests and four intentional opt-in skips. Ruff,
formatting, mypy, shell syntax, and diff checks passed. The live k3d test and direct evidence run
both passed, and the cleanup check returned an empty cluster list.

## Residual risk and next gate

The demonstrated resource is deliberately small and reversible. A future hosted or application
deployment needs its own immutable artifact provenance, health probes, provider trust, cost plan,
backup or forward-recovery strategy, production authentication, and owner approval. Phase 5
should add scale or multi-tenancy only when measured requirements justify it.
