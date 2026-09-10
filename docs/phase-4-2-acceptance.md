# Phase 4.2 Acceptance Record

## Scope

Phase 4.2 is complete locally as of 2026-09-08. The accepted scope is the USD 0 Docker/k3d
identity boundary, OpenTofu saved-plan validation, a deny-by-default broker, a metadata-only fake,
and one real Kubernetes TokenRequest broker qualification. It does not authorize `tofu apply`, an
application deployment, a persistent cluster, a hosted environment, or production credentials.

## Implemented controls

| Boundary | Evidence |
|---|---|
| Default denial | `ControlPlaneService` still installs `DenyCredentialBroker`; no configuration or ambient credential activates the Kubernetes broker |
| Explicit opt-in | `KubernetesTokenRequestBroker` rejects issuance unless constructed with `enabled=True` |
| Exact request binding | Environment, adapter, operation, resource scope, audience, subject, plan digest, and 600-second lifetime are checked before target contact |
| Typed invocation | `KubectlTokenRequester` uses a fixed argument vector, explicit kubeconfig, bounded timeout, no shell, and sanitized failures |
| Returned-claim verification | Issuer, audience, subject, embedded namespace/service-account identity, `iat`, `exp`, exact lifetime, freshness, and current validity are checked |
| Credential containment | The token is never placed in `WorkloadCredentialHandle`, logs, documentation, or evidence; only a random reference and issuance metadata return |
| Least privilege | Two intended namespace reads passed; secret, write, lateral namespace, `kube-system`, and cluster-scope access were denied |
| Immutable binding | The live request used operation `OBSERVE` and plan digest `c223f5862de0318535f92af6f4ba0f1e24490aab8b166aae5f82e0bc3aaf2581`, the SHA-256 of the applied identity manifest |
| Teardown | The named k3d cluster, containers, network, image volume, and temporary kubeconfig were removed |
| Cost | No cloud account or resource was created; incremental infrastructure cost remained USD 0 |

## Negative-path coverage

Tests prove denial before issuance when activation is absent or any environment, adapter,
operation, scope, audience, subject, lifetime, or plan-digest binding is invalid. Separate tests
reject malformed, stale, wrong-audience, wrong-subject, and wrong-lifetime credentials. Requester
stderr and unexpected exception canaries do not appear in returned error text. The opt-in live
test runs the concrete broker against ephemeral k3d and verifies sanitized output and cleanup.

## Remaining boundary

Phase 4.3 must introduce one narrowly scoped non-production adapter and a separate approved
credential-delivery design. It must bind execution to the exact approved saved plan, commit intent
before issuance, prevent token persistence, observe the target independently, and stop at
`ROLLBACK_REQUIRED` on failed verification. Phase 4.2 evidence alone grants no mutation authority.
