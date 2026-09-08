# Phase 4.1 Acceptance Record

## Decision and boundary

The project owner approved the provider-neutral Phase 4 design on 2026-09-08. Phase 4.1
implements only the durable control and deterministic dry-run foundation. It does not select a
hosting provider, create an account or resource, spend money, obtain a deployment credential,
contact an external target, or authorize production deployment.

## Implemented evidence

| Control | Evidence |
|---|---|
| Immutable inventory | Operator-owned environment records include repository, branch, resource scope, adapter and policy version, verification policy, rollback reference, creator, and configuration digest |
| Production boundary | Phase 4.1 rejects `PRODUCTION`, non-dry-run providers, external account/region targets, unknown adapters, and every credential-requiring adapter |
| Immutable plan | Canonical plan content binds the environment configuration digest, confirmed merge revision, SHA-256 artifact digests, ordered typed operations, declared impact, probes, rollback reference, and policy version |
| Separate approval | `DEPLOY` approval requires an authenticated human and exact environment, merged revision, plan digest, target, policy version, rationale, and expiry |
| One-time authority | Approval remains unconsumed until execution intent is committed, is rejected after expiry, and is consumed once before adapter invocation |
| Typed execution | Only `VERIFY_ARTIFACT`, `APPLY_RELEASE`, and `UPDATE_SERVICE` operations with allowlisted fields and bound artifact digests are accepted; arbitrary commands are absent |
| Safe adapter | `dry-run-v1` requires no credential, reports no target contact and no change, observes the exact revision and plan digest, and emits independent verification evidence |
| Durable evidence | Migration 0009 adds environment, plan, attempt, verification, and rollback records plus deployment bindings on approvals and the authoritative merged revision on workflows |
| Containment | Successful simulation remains `AWAITING_DEPLOYMENT_APPROVAL`; it cannot be confused with a real `DEPLOYED` result |

## Verification

The local release gate includes Ruff lint and format checks, strict mypy analysis, the complete
non-live pytest suite, dedicated deployment API and negative-path tests, a PostgreSQL migration
to revision `0009`, and the PostgreSQL integration test. Security tests cover production denial,
untyped-operation denial, agent authorization denial, wrong-plan approval rejection, expired
approval rejection, one-time consumption, idempotent replay, no credential request, no external
target contact, exact revision observation, and audit-chain validity.

## Remaining gates

Phase 4.2 requires a separate owner decision selecting the first non-production hosting target,
cost ceiling, workload-identity mechanism, maximum credential lifetime, and trust policy. No
real adapter or credential broker should be activated before those decisions are documented.
