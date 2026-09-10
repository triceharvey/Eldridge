# ADR 0011: Local Kubernetes TokenRequest Broker

## Status

Accepted on 2026-09-08.

## Context

Phase 4.2 requires one real, short-lived workload-identity mechanism without adding cloud cost or
quietly converting the control plane into a credential store. The local k3d exercise already
proved namespace-scoped RBAC and Kubernetes projected service-account identity. The remaining
decision is how to connect that mechanism to Eldridge's credential-broker contract without
exposing token material to plans, APIs, audit records, logs, or model-visible output.

## Decision

Use the Kubernetes TokenRequest API for the local Phase 4.2 qualification. A narrow typed client
invokes `kubectl create token` without a shell and with an explicit temporary kubeconfig. The
concrete broker remains disabled unless its constructor receives `enabled=True`, validates every
request against one exact environment, adapter, operation, namespace scope, audience, subject,
manifest digest, and 600-second lifetime, and independently validates the issuer, trust, embedded
workload identity, and time claims in the issued JWT. It then discards the token and returns only
a random opaque reference and sanitized issuance metadata through the existing `CredentialBroker`
interface.

Normal control-plane construction continues to install the deny-all broker. This qualification
does not enable a credential-requiring deployment adapter. SPIFFE/SPIRE is deferred until a
measured need for cross-workload or cross-cluster federation justifies its additional trust-domain
operations.

## Consequences

- A kubeconfig or installed binary does not activate token issuance.
- Policy widening is rejected before the TokenRequest API is contacted.
- Returned subject, audience, lifetime, issuance time, and expiration are verified before a
  handle is produced.
- Token values and command stderr are excluded from returned metadata and stable errors.
- The local validation process briefly handles token material in memory, then discards it; it
  does not persist, forward, or use the credential to mutate the cluster.
- A Phase 4.3 adapter must define a separate least-authority delivery mechanism and may not infer
  deployment authority from this identity qualification.

## Alternatives

- **SPIFFE/SPIRE now:** valuable for federation and workload attestation, but unjustified for one
  ephemeral local cluster.
- **Long-lived service-account secret:** rejected because it weakens expiry and revocation.
- **Return the raw token in the broker handle:** rejected because it would expose credential
  material to orchestration and persistence paths.
- **Enable from ambient kubeconfig discovery:** rejected because credential presence is not
  operator authorization.
