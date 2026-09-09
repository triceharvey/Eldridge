# ADR 0012: Bounded Local k3d Deployment Adapter

## Status

Accepted on 2026-09-09.

## Context

Phase 4.3 requires one real non-production deployment path that preserves Eldridge's exact-plan,
human-approval, short-lived-identity, evidence, and failure-containment boundaries. The approved
first target remains an ephemeral local k3d cluster with a USD 0 infrastructure ceiling. A full
application deployment would add image publication, registry trust, service health, and recovery
questions that are not necessary to prove the adapter contract.

## Decision

Use one pre-created `eldridge-release` ConfigMap in the `eldridge-validation` namespace as the
Phase 4.3 release marker. The `local-k3d-configmap-v1` adapter accepts exactly one typed
`UPDATE_SERVICE` operation bound to one SHA-256 artifact, a 40-character Git revision, the
immutable plan digest, three fixed verification probes, and the
`restore-previous-release-marker-v1` rollback reference.

The control plane must commit a durable `RUNNING` attempt and consume the exact unexpired human
approval before it creates the plan-bound broker or requests a credential. A fresh Kubernetes
TokenRequest is delivered only inside the trusted adapter callback. The adapter may use that
credential only over loopback HTTPS to read and update the named ConfigMap. It then performs a
separate target read and verifies the revision, plan digest, and artifact digest. Stored evidence
contains only the opaque credential reference and validated metadata, never the token value.

The service and adapter remain disabled unless explicitly assembled with the local adapter, an
enabled session-broker factory, and `enable_local_deployment=True`. Production, hosted, lateral,
create, delete, secret, arbitrary resource, arbitrary URL, and generic command authority are not
part of this decision.

## Consequences

- Replaying a successful service idempotency key returns the durable attempt without another
  credential or target call.
- Replaying the same adapter operation against matching target data performs no second update.
- A timeout or transport failure during the update becomes `UNKNOWN` and cannot be automatically
  retried.
- A confirmed change followed by failed observation becomes `ROLLBACK_REQUIRED`.
- Failures proven to occur before mutation become `FAILED`.
- The role grants only `get` and `update` on the exact release-marker ConfigMap in addition to the
  Phase 4.2 observation permissions.
- This validates the deployment control path, not a full Eldridge application release or the
  rollback path. Phase 4.4 remains required.

## Alternatives

- **Deploy the full application now:** deferred until image provenance, registry, health probes,
  and rollback behavior can be evaluated together.
- **Allow arbitrary ConfigMaps or Kubernetes objects:** rejected because it widens resource and
  operation authority beyond the proof required by Phase 4.3.
- **Use a long-lived kubeconfig credential:** rejected because it bypasses audience, subject,
  lifetime, and exact-plan controls.
- **Retry an ambiguous write:** rejected because a second mutation could compound an unknown
  external outcome.
