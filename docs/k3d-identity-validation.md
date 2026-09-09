# Ephemeral k3d Identity-Boundary Validation

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
| Token subject | `system:serviceaccount:eldridge-validation:deployment-worker` |
| Token audience | `eldridge-local-k3d` |
| Token lifetime | 600 seconds |
| Persistent token mount | Disabled with `automountServiceAccountToken: false` |
| Teardown | No named cluster, container, network, or image volume remained |

The token itself was piped directly into a local decoder and was never printed or persisted.
Only its audience, subject, and calculated lifetime were emitted. Kubernetes 1.35 rejected a
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

This exercise proves the local Kubernetes identity and teardown boundary. It does not authorize
`tofu apply`, deploy the Eldridge application, qualify SPIFFE/SPIRE, create a hosted environment,
or satisfy Phase 4.3 deployment-adapter and Phase 4.4 recovery-exercise criteria.
