#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLUSTER_NAME="eldridge-phase4-validation"
K3S_IMAGE="rancher/k3s@sha256:2074403abe1bded11ef3dde09d457e13be8e0b64c218b1c4f8269b4565cfbc65"
VALIDATION_TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/eldridge-k3d.XXXXXX")"
VALIDATION_KUBECONFIG="${VALIDATION_TMP_DIR}/kubeconfig"

cleanup() {
  k3d cluster delete "${CLUSTER_NAME}" >/dev/null 2>&1 || true
  if [[ -d "${VALIDATION_TMP_DIR}" && "${VALIDATION_TMP_DIR}" == *"/eldridge-k3d."* ]]; then
    rm -r "${VALIDATION_TMP_DIR}"
  fi
}
trap cleanup EXIT INT TERM

for tool in docker k3d kubectl python3 tofu; do
  command -v "${tool}" >/dev/null || {
    echo "required tool is unavailable: ${tool}" >&2
    exit 1
  }
done

if k3d cluster list -o json | python3 -c \
  'import json,sys; name=sys.argv[1]; raise SystemExit(0 if any(item.get("name") == name for item in json.load(sys.stdin)) else 1)' \
  "${CLUSTER_NAME}"; then
  echo "refusing to reuse or delete an existing cluster named ${CLUSTER_NAME}" >&2
  exit 1
fi

[[ "$(tofu version -json | python3 -c 'import json,sys; print(json.load(sys.stdin)["terraform_version"])')" == "1.12.6" ]] || {
  echo "OpenTofu must be pinned to 1.12.6" >&2
  exit 1
}
[[ "$(k3d version | sed -n 's/^k3d version v//p')" == "5.9.0" ]] || {
  echo "k3d must be pinned to 5.9.0" >&2
  exit 1
}
[[ "$(docker image inspect "${K3S_IMAGE}" --format '{{index .RepoDigests 0}}')" == "${K3S_IMAGE}" ]] || {
  echo "the pinned k3s image digest is unavailable or does not match" >&2
  exit 1
}

k3d cluster create --config "${SCRIPT_DIR}/phase4-validation.yaml"
k3d kubeconfig get "${CLUSTER_NAME}" >"${VALIDATION_KUBECONFIG}"
# k3d 5.9.0 emits 0.0.0.0 as the API endpoint on macOS. That is a bind
# address, not a connectable destination, so constrain the temporary config to
# the loopback interface without touching the user's default kubeconfig.
sed 's#server: https://0\.0\.0\.0:#server: https://127.0.0.1:#' \
  "${VALIDATION_KUBECONFIG}" >"${VALIDATION_KUBECONFIG}.loopback"
mv "${VALIDATION_KUBECONFIG}.loopback" "${VALIDATION_KUBECONFIG}"
export KUBECONFIG="${VALIDATION_KUBECONFIG}"

kubectl wait --for=condition=Ready node --all --timeout=90s
kubectl apply -f "${SCRIPT_DIR}/identity-boundary.yaml"

SUBJECT="system:serviceaccount:eldridge-validation:deployment-worker"
assert_permission() {
  local expected="$1"
  local verb="$2"
  local resource="$3"
  local namespace="$4"
  local actual
  actual="$(kubectl auth can-i "${verb}" "${resource}" --namespace "${namespace}" --as "${SUBJECT}" || true)"
  if [[ "${actual}" != "${expected}" ]]; then
    echo "RBAC assertion failed: expected ${expected}, got ${actual}: ${verb} ${resource} in ${namespace}" >&2
    exit 1
  fi
  printf 'rbac expected=%s actual=%s verb=%s resource=%s namespace=%s\n' \
    "${expected}" "${actual}" "${verb}" "${resource}" "${namespace}"
}

assert_permission yes get pods eldridge-validation
assert_permission yes list deployments.apps eldridge-validation
assert_permission no get secrets eldridge-validation
assert_permission no create pods eldridge-validation
assert_permission no get pods eldridge-denied
assert_permission no get pods kube-system
assert_permission no create namespaces default

kubectl create token deployment-worker \
  --namespace eldridge-validation \
  --audience eldridge-local-k3d \
  --duration 10m | python3 -c '
import base64
import json
import sys

token = sys.stdin.read().strip()
parts = token.split(".")
if len(parts) != 3:
    raise SystemExit("projected token is not a JWT")
payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
expected_subject = "system:serviceaccount:eldridge-validation:deployment-worker"
audience = payload.get("aud", [])
audience = [audience] if isinstance(audience, str) else audience
lifetime = int(payload["exp"]) - int(payload["iat"])
if payload.get("sub") != expected_subject:
    raise SystemExit("projected token subject is not bound")
if audience != ["eldridge-local-k3d"]:
    raise SystemExit("projected token audience is not bound")
if lifetime < 1 or lifetime > 600:
    raise SystemExit("projected token lifetime exceeds the Kubernetes ten-minute minimum")
print(json.dumps({"aud": audience, "sub": payload["sub"], "lifetime_seconds": lifetime}, sort_keys=True))
'

docker image inspect \
  "${K3S_IMAGE}" \
  --format 'image_id={{.Id}} repo_digests={{join .RepoDigests ","}}'
kubectl get namespace eldridge-validation -o jsonpath='namespace={.metadata.name} phase={.status.phase}{"\n"}'
printf 'cluster=%s result=passed cleanup=armed\n' "${CLUSTER_NAME}"
