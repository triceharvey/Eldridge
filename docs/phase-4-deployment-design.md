# Phase 4 Controlled Deployment Design

## Status and boundary

The project owner approved this Phase 4 design on 2026-09-08. That approval authorizes the
provider-neutral implementation sequence; it does not authorize a cloud
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

Status: implemented locally. The API accepts only typed, artifact-bound operations; rejects
production and external-target environment records; commits execution intent before invoking
the adapter; consumes one exact, unexpired deployment approval; and records independent
observation and verification evidence. The dry-run adapter cannot request credentials or
contact an external target and does not move the workflow to `DEPLOYED`.

### Phase 4.2 — Short-lived identity boundary

Add one credential-broker interface and a fake broker. Qualify one real workload-identity
mechanism only after the hosting target is selected and its trust policy is reviewed.
OpenTofu is the approved provider-neutral infrastructure layer; ADR 0009 defines the saved-plan,
version-pinning, state-security, and exact-apply boundary. ADR 0010 selects the zero-cost local
profile while leaving the first hosted profile as a later owner decision.

The owner approved a zero-dollar local Docker/k3d target on 2026-09-08. The implementation uses a
deny-by-default credential broker, a metadata-only fake broker, and an explicitly enabled local
Kubernetes TokenRequest broker. The real broker validates exact request and returned-token
bindings, discards the token, and exposes only sanitized handle metadata. SPIFFE/SPIRE is deferred
until cross-workload federation is justified. A temporary hosted exercise has a separate maximum
total budget of approximately USD 5 and still requires a selected provider, exact plan, and
execution approval before any resource is created. Azure and persistent hosted K3s remain future
alternatives.

The saved-plan validation slice is implemented with pinned CLI/provider policy, exact resource
and action allowlists, destructive-change and drift rejection, sensitive-artifact containment,
SHA-256 plan/lock bindings, a zero-cost check, sanitized evidence, and a no-subprocess local
simulation adapter. A bounded ephemeral k3d exercise also passed with a digest-pinned K3s image,
namespace-scoped read access, explicit sensitive/write/lateral denials, a subject- and
audience-bound projected service-account token, and automatic teardown. Real OpenTofu apply and
application deployment remain disabled.

### Phase 4.3 — One non-production adapter

Implement one narrowly scoped adapter for the selected target. Prove revision binding,
idempotency, least privilege, timeout containment, observation, and post-deploy verification.

Status: implemented and validated locally on 2026-09-09. The selected environment is
`eldridge-local-k3d`; the only mutable resource is the pre-created
`configmap/eldridge-release`. The fixed probes verify revision, plan digest, and artifact digest,
and the recorded rollback policy is `restore-previous-release-marker-v1`. The service commits
intent and consumes the exact approval before using a fresh plan-bound broker. The credential is
available only inside the adapter callback, and target evidence is observed separately from the
update response. The first live operation changed and verified the marker; its exact replay
verified without another update. Ambiguous writes become non-replayable `UNKNOWN` attempts.

### Phase 4.4 — Recovery exercise

Inject a failed verification, require a rollback decision, execute the bounded rollback, and
retain recovery-time and audit evidence. Production remains disabled until the owner approves
the evidence and residual risk.

## Decisions still requiring owner approval

1. The provider-neutral Phase 4 architecture was approved on 2026-09-08.
2. OpenTofu was approved as the provider-neutral infrastructure layer on 2026-09-08.
3. The first non-production target is local Docker/k3d with a USD 0 infrastructure ceiling.
4. The local Kubernetes TokenRequest broker and exact 600-second lifetime were approved and
   qualified on 2026-09-08; SPIFFE/SPIRE remains an optional future federation layer.
5. The owner authorized the Phase 4.3 local environment inventory, fixed verification probes,
   and rollback-policy reference on 2026-09-09. This does not authorize rollback execution.
6. Approve a separate production-readiness review after the non-production recovery exercise.
