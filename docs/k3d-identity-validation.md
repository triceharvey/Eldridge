# Ephemeral k3d Identity and Deployment-Boundary Validation

## Result

The approved zero-cost Phase 4.2 Kubernetes exercise passed on 2026-09-08. It created one
ephemeral, single-server k3d cluster on the owner's existing Docker installation, validated a
namespace-scoped workload identity, captured only sanitized evidence, and automatically deleted
the cluster. It created no cloud account or resource, contacted no commercial provider, and
incurred no infrastructure charge.

The reproducible assets are:

- `deployment/k3d/phase4-validation.yaml` for the minimal cluster;
- `deployment/k3d/identity-boundary.yaml` for the service account and RBAC boundary; and
- `deployment/k3d/validate-boundary.sh` for preflight, assertions, sanitized evidence, and
  teardown.

## Verified evidence

| Control | Observed result |
|---|---|
| k3d | `5.9.0` |
| K3s image | `rancher/k3s@sha256:2074403abe1bded11ef3dde09d457e13be8e0b64c218b1c4f8269b4565cfbc65` |
| Node | One server reached `Ready` |
| Intended access | `get pods` and `list deployments.apps` in `eldridge-validation`: allowed |
| Sensitive/write access | `get secrets` and `create pods` in `eldridge-validation`: denied |
| Lateral access | `get pods` in `eldridge-denied` and `kube-system`: denied |
| Cluster-scope access | `create namespaces`: denied |
| Exact release-marker access | `get` and `update` `configmap/eldridge-release`: allowed |
| Resource widening | create any ConfigMap, update another ConfigMap, and delete the release marker: denied |
| Token subject | `system:serviceaccount:eldridge-validation:deployment-worker` |
| Token audience | `eldridge-local-k3d` |
| Token lifetime | 600 seconds |
| Persistent token mount | Disabled with `automountServiceAccountToken: false` |
| Teardown | No named cluster, container, network, or image volume remained |

On 2026-09-09, the same ephemeral boundary completed the Phase 4.3 release-marker exercise
against Git revision `85aafcb89d46a7798ae0462fbdeb3cada4e70fc6`. A short-lived token was
delivered only inside the adapter callback. The first operation updated and verified the exact
revision, plan digest, and artifact digest; the replay verified the same state without another
update. Four intended RBAC checks were allowed and eight widening checks were denied.

The Phase 4.4 exercise then deliberately replaced the just-deployed revision before independent
verification. Eldridge contained the workflow in `ROLLBACK_REQUIRED`, consumed a separate exact
rollback approval, requested a fresh credential bound to operation `ROLLBACK`, and restored only
the recorded prior marker. A separate read verified the complete snapshot, the workflow entered
`ROLLED_BACK`, the audit chain remained valid, and measured recovery execution was 45 ms.

The token is now requested through Eldridge's concrete `KubernetesTokenRequestBroker`, validated
in memory, discarded, and never printed or persisted. Only the non-secret reference scheme,
broker and request bindings, audience, subject, issuance and expiry, and calculated lifetime are
emitted. Kubernetes 1.35 rejected a
five-minute TokenRequest because the API requires at least ten minutes, so this validation uses
the enforced 600-second minimum. Eldridge's simulated credential broker retains its separate
five-minute policy because no Kubernetes TokenRequest is issued there.

## Reproduction boundary

Run the live test only on a machine where creating and deleting the exact named local cluster is
acceptable:

```bash
CONTROL_PLANE_RUN_K3D_TEST=1 .venv/bin/pytest -m k3d -q
```

The script refuses to reuse an existing `eldridge-phase4-validation` cluster, uses a temporary
kubeconfig without changing the default context, and arms cleanup before cluster creation. Its
trap deletes only that exact cluster name and its validated temporary directory, including after
an assertion failure. Container images remain in the local Docker cache so later reproductions do
not require another pull.

This exercise proves the local Kubernetes identity, concrete credential broker, bounded
deployment adapter, redaction, idempotency, verification, controlled recovery, and teardown
boundaries through Phase 4.4. It does not authorize `tofu apply`, deploy the Eldridge application,
or create a hosted or production environment. SPIFFE/SPIRE remains deferred unless federation
needs justify it.
