# ADR 0008: Controlled Deployment Boundary

- Status: Accepted
- Date: 2026-09-08
- Accepted by: project owner on 2026-09-08

## Context

Eldridge can confirm an exact Git merge, but merge authority must remain separate from
deployment authority. A production-minded deployment path must handle short-lived credentials,
external side effects, ambiguous outcomes, verification failure, and rollback without letting
model output become authorization.

## Decision

Introduce a provider-neutral deployment subsystem after explicit owner approval. Store an
operator-owned environment inventory and immutable, digest-bound deployment plans. Require a
separate expiring human `DEPLOY` approval for the exact environment, revision, plan digest, and
policy version. Commit execution intent before external calls, use only typed adapters and
short-lived credential handles, and require independent post-deploy verification.

An ambiguous call enters reconciliation and is not retried automatically. A changed target
that fails verification enters `ROLLBACK_REQUIRED`. Begin with a deterministic dry-run adapter;
the first real adapter targets only an explicitly approved non-production environment.

## Consequences

- Merge approval cannot be reused as deployment approval.
- Cloud-provider selection remains deferred without blocking durable core implementation.
- The data model and API become larger because plans, attempts, observations, verification,
  and rollback evidence remain distinct records.
- Operators must maintain environment and recovery policies outside untrusted repositories.
- Production deployment remains unavailable until real non-production and rollback evidence
  exists.

## Rejected alternatives

- **Run model-generated deployment commands:** arbitrary commands cannot provide a stable
  authorization or isolation boundary.
- **Use one approval for merge and deployment:** the targets, risks, timing, and evidence are
  different.
- **Store permanent cloud keys in Eldridge:** compromise would outlive a task and defeat scoped
  authorization.
- **Automatically retry timeouts:** the first request may already have changed the target.
- **Choose Kubernetes or a cloud provider before evidence:** that would couple core safety
  invariants to an unapproved platform.
