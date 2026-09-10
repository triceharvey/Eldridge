from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from control_plane.credentials import (
    CredentialOperation,
    KubectlTokenRequester,
    KubernetesTokenRequestBroker,
    WorkloadCredentialRequest,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate the local Kubernetes credential broker")
    parser.add_argument("--kubeconfig", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args()

    plan_digest = hashlib.sha256(args.manifest.read_bytes()).hexdigest()
    request = WorkloadCredentialRequest(
        environment_id="eldridge-local-k3d",
        adapter_id="k3d-identity-observer-v1",
        plan_digest=plan_digest,
        operation=CredentialOperation.OBSERVE,
        audience="eldridge-local-k3d",
        subject="system:serviceaccount:eldridge-validation:deployment-worker",
        resource_scope=("namespace/eldridge-validation",),
        lifetime_seconds=600,
    )
    broker = KubernetesTokenRequestBroker(
        requester=KubectlTokenRequester(kubeconfig=args.kubeconfig),
        environment_id=request.environment_id,
        adapter_id=request.adapter_id,
        plan_digest=request.plan_digest,
        namespace="eldridge-validation",
        service_account="deployment-worker",
        audience=request.audience,
        issuer="https://kubernetes.default.svc.cluster.local",
        resource_scope=request.resource_scope,
        operation=request.operation,
        enabled=True,
        lifetime_seconds=request.lifetime_seconds,
    )
    handle = broker.issue(request)
    print(
        json.dumps(
            {
                "adapter_id": request.adapter_id,
                "aud": handle.audience,
                "broker_id": handle.broker_id,
                "environment_id": handle.environment_id,
                "expires_at": handle.expires_at.isoformat(),
                "issued_at": handle.issued_at.isoformat(),
                "lifetime_seconds": int((handle.expires_at - handle.issued_at).total_seconds()),
                "operation": handle.operation.value,
                "plan_digest": handle.plan_digest,
                "reference_scheme": handle.reference.split(":", 1)[0],
                "resource_scope": list(handle.resource_scope),
                "simulated": handle.simulated,
                "sub": handle.subject,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
