# Phase 4 Controlled Deployment Design

## Status and boundary

This document proposes the Phase 4 design for owner approval. It does not authorize a cloud
account, paid service, public endpoint, production credential, infrastructure change, or
deployment. The first implementation target is a deterministic dry-run adapter, followed by
one explicitly selected non-production environment.

The deployment subsystem extends the existing control-plane invariants. An AI agent may
prepare a plan and analyze evidence, but deterministic policy and an eligible human remain the
only authorities that can approve execution.

## Security invariants

1. A merge approval never authorizes deployment.
2. A deployment approval binds the workflow, merged revision, immutable environment ID,
   deployment-plan digest, policy version, approver, rationale, and expiry.
3. The approved plan cannot be edited. Any material change creates a new plan digest and
   invalidates the approval.
4. The worker requests short-lived, audience-bound credentials only after consuming a valid
   approval. Long-lived cloud access keys are not accepted by the production design.
5. Executors expose typed operations; model-generated shell commands are not a deployment
   interface.
6. The target adapter may access only its allowlisted environment and resources.
7. Successful execution is not `DEPLOYED` until independent post-deploy verification passes.
8. An ambiguous external result enters reconciliation. It is never automatically retried.
9. Failed verification after a possible change enters `ROLLBACK_REQUIRED`; rollback requires
   its own policy decision and complete evidence.
10. Every intent, approval, credential issuance reference, operation, observation,
    reconciliation, and rollback decision is auditable without storing credential material.

## Authoritative records

### Environment inventory

An operator-owned, versioned environment record contains:

- opaque environment ID and human-readable name;
- classification (`DEVELOPMENT`, `STAGING`, or `PRODUCTION`);
- provider and account, project, or tenant identifiers;
- permitted region and resource scopes;
- deployment adapter and immutable adapter-policy version;
- workload-identity issuer, audience, and allowed subject;
- allowed repository and base branch;
- required checks and artifact attestations;
- verification and rollback policy references; and
- active or disabled state.

The API accepts only an environment ID. Repository content, issue text, model output, and a
caller-supplied URL cannot create or widen an environment record.

### Deployment plan

A deployment plan is an immutable proposal containing the workflow ID, merge commit, artifact
digests, environment ID, ordered typed operations, declared impact, verification probes,
rollback reference, and policy version. Canonical serialization produces the SHA-256 plan
digest used by approval and audit records.

### Deployment attempt

Each execution creates a new attempt; retries never overwrite history. Attempt states are:

```text
PENDING -> RUNNING -> VERIFYING -> SUCCEEDED
                     |            |
                     |            +-> ROLLBACK_REQUIRED
                     +-> UNKNOWN -> RECONCILED
RUNNING -> FAILED
```

`FAILED` is valid only when the adapter can prove that no target change occurred. `UNKNOWN`
means the external outcome may have changed and requires a read-only reconciliation command.
`ROLLBACK_REQUIRED` means a change occurred but verification did not establish the approved
outcome.

## Interfaces

### Deployment adapter

The provider-neutral adapter contract exposes bounded operations:

- `validate_plan` confirms schema, environment scope, revision, artifact digests, and adapter
  support without external mutation;
- `execute` accepts the immutable plan and a short-lived credential handle;
- `observe` reads provider state using an idempotency and correlation key;
- `verify` runs operator-defined health, version, and policy probes; and
- `rollback` executes only the approved rollback reference and returns structured evidence.

No generic shell, arbitrary provider API request, or caller-selected credential is part of the
contract.

### Credential broker

The broker accepts an environment ID, adapter identity, plan digest, and operation. It returns
a short-lived credential handle narrowed by audience, resource scope, and maximum lifetime.
The control plane records issuance metadata and expiry, never the credential value. The
initial dry-run adapter uses no credential; a real adapter requires workload federation or an
equivalent short-lived mechanism.

## Command flow

1. An eligible human requests a deployment plan for a workflow already confirmed `MERGED`.
2. The service resolves the immutable environment record and merged revision.
3. A deterministic builder validates artifacts, checks, verification policy, and rollback
   reference, then stores the immutable plan and digest.
4. The workflow enters `AWAITING_DEPLOYMENT_APPROVAL`.
5. A different, explicit `DEPLOY` approval binds the environment, revision, plan digest, policy
   version, and expiry.
6. The worker commits execution intent before obtaining credentials or calling the adapter.
7. The adapter executes with a unique idempotency key and short-lived credential.
8. Independent observation and verification establish the resulting revision and health.
9. Only verified success enters `DEPLOYED`; ambiguous or unhealthy outcomes enter their
   containment states.

## Failure policy

| Condition | Required disposition |
|---|---|
| Approval expired or binding changed | Reject before credential issuance |
| Credential issuance failed | `FAILED`; target was not contacted |
| Adapter rejected the plan before mutation | `FAILED` with validation evidence |
| Timeout or transport loss after request | `UNKNOWN`; prohibit automatic retry |
| Provider reports operation still active | Remain contained and reconcile later |
| Target revision differs from approved revision | `ROLLBACK_REQUIRED` |
| Health or security verification fails | `ROLLBACK_REQUIRED` |
| Rollback outcome is ambiguous | Remain `ROLLBACK_REQUIRED` and escalate |

## Implementation slices

### Phase 4.1 — Durable plan and dry-run adapter

Add environment, plan, attempt, verification, and rollback records; exact-binding approval;
command APIs; deterministic plan hashing; a no-credential dry-run adapter; and negative tests.
No external mutation occurs.

### Phase 4.2 — Short-lived identity boundary

Add one credential-broker interface and a fake broker. Qualify one real workload-identity
mechanism only after the hosting target is selected and its trust policy is reviewed.

### Phase 4.3 — One non-production adapter

Implement one narrowly scoped adapter for the selected target. Prove revision binding,
idempotency, least privilege, timeout containment, observation, and post-deploy verification.

### Phase 4.4 — Recovery exercise

Inject a failed verification, require a rollback decision, execute the bounded rollback, and
retain recovery-time and audit evidence. Production remains disabled until the owner approves
the evidence and residual risk.

## Decisions still requiring owner approval

1. Approve this provider-neutral Phase 4 architecture before Phase 4.1 implementation.
2. Select the first non-production hosting target and cost ceiling before Phase 4.2.
3. Select its workload-identity mechanism and maximum credential lifetime.
4. Approve environment inventory, verification probes, and rollback policy before any real
   deployment.
5. Approve a separate production-readiness review after the non-production recovery exercise.
