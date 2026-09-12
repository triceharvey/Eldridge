# ADR-0027: Extract the controlled deployment and recovery boundary

- Status: Accepted
- Date: 2026-09-11

## Context

`ControlPlaneService` still implemented environment registration, immutable deployment plans,
approval consumption, dry runs, local credentialed execution, verification, rollback, and failure
containment. This was the largest remaining domain in the facade and combined sensitive credential
lifetime rules with unrelated orchestration responsibilities.

Deployment and recovery form one transactional safety boundary: the same immutable plan, revision,
environment policy, credential audience, approval, and recorded attempt must govern both forward and
recovery operations.

## Decision

Create `DeploymentRecoveryService` as an in-process component owning environment policy, immutable
plans, dry-run and local execution, credential-broker sessions, verification, rollback, failure
classification, and deployment read models. `ControlPlaneService` retains its public signatures and
delegates to the component.

The component receives only the configured adapter registry, credential broker or session factory,
the explicit local-deployment activation flag, and the canonical workflow transition operation. It
does not import the facade. Existing production prohibition, local-k3d restrictions, exact approval
bindings, expiration checks, single-use consumption, redacted credential handles, outcome-unknown
containment, and rollback requirements remain unchanged.

## Alternatives considered

- Create a separate deployment network service. Rejected because the current local scale does not
  justify distributed transactions or another credential-bearing deployment unit.
- Separate deployment and rollback immediately. Rejected because recovery must stay bound to the
  exact failed attempt and immutable plan.
- Let the component mint or retain credentials. Rejected because credentials must remain ephemeral
  inside the broker callback and never enter durable response or audit records.

## Consequences

- Credential-bearing code and recovery logic are independently reviewable.
- API contracts and the USD 0, disabled-by-default posture remain stable.
- The facade is reduced substantially without creating new infrastructure.
- Windsurf integration and shared facade compatibility helpers remain the last internal decomposition
  boundary.

## Failure behavior

Unapproved adapters, production targets, stale plans, expired or mismatched approvals, and unavailable
credential sessions fail before target contact. Ambiguous contacted-target outcomes remain `UNKNOWN`;
failed verification enters `ROLLBACK_REQUIRED`; failed or ambiguous rollback remains recorded and
does not fabricate recovery.
