from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import ssl
from pathlib import Path

import httpx

from control_plane.credentials import (
    CredentialOperation,
    KubectlTokenRequester,
    KubernetesTokenRequestBrokerFactory,
)
from control_plane.deployment import DeploymentPlan
from control_plane.kubernetes_deployment import LocalK3dConfigMapAdapter


def _cluster_connection(kubeconfig: Path) -> tuple[str, ssl.SSLContext]:
    content = kubeconfig.read_text()
    servers = re.findall(r"^\s*server:\s*(\S+)\s*$", content, flags=re.MULTILINE)
    authorities = re.findall(
        r"^\s*certificate-authority-data:\s*(\S+)\s*$", content, flags=re.MULTILINE
    )
    if len(servers) != 1 or len(authorities) != 1:
        raise SystemExit("temporary kubeconfig does not contain one bounded cluster")
    try:
        authority = base64.b64decode(authorities[0], validate=True).decode()
    except (ValueError, UnicodeDecodeError):
        raise SystemExit("temporary kubeconfig certificate authority is invalid") from None
    context = ssl.create_default_context(cadata=authority)
    return servers[0], context


def _plan(revision: str, artifact_digest: str) -> DeploymentPlan:
    values = {
        "environment_id": "eldridge-local-k3d",
        "revision": revision,
        "artifact_digests": [artifact_digest],
        "operations": [
            {
                "kind": "UPDATE_SERVICE",
                "resource_id": "configmap/eldridge-release",
                "artifact_digest": artifact_digest,
            }
        ],
        "verification_probes": [
            "revision-match",
            "plan-digest-match",
            "artifact-digest-match",
        ],
        "rollback_reference": "restore-previous-release-marker-v1",
        "policy_version": "deployment/local-k3d-v1",
    }
    digest = hashlib.sha256(
        json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return DeploymentPlan(
        plan_id="phase-4-3-live-validation",
        workflow_id="phase-4-3-live-validation",
        environment_id=values["environment_id"],
        revision=revision,
        artifact_digests=(artifact_digest,),
        operations=tuple(values["operations"]),
        verification_probes=tuple(values["verification_probes"]),
        rollback_reference=values["rollback_reference"],
        policy_version=values["policy_version"],
        digest=digest,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate the local k3d deployment adapter")
    parser.add_argument("--kubeconfig", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--revision", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.revision):
        raise SystemExit("validation revision must be a lowercase Git SHA-1")

    manifest_digest = hashlib.sha256(args.manifest.read_bytes()).hexdigest()
    artifact_digest = f"sha256:{manifest_digest}"
    plan = _plan(args.revision, artifact_digest)
    server, context = _cluster_connection(args.kubeconfig)
    with httpx.Client(
        base_url=server,
        verify=context,
        timeout=10,
        trust_env=False,
    ) as client:
        adapter = LocalK3dConfigMapAdapter(client=client, api_server=server)
        request = adapter.credential_request(plan)
        broker_factory = KubernetesTokenRequestBrokerFactory(
            requester=KubectlTokenRequester(kubeconfig=args.kubeconfig),
            environment_id=request.environment_id,
            adapter_id=request.adapter_id,
            namespace="eldridge-validation",
            service_account="deployment-worker",
            audience=request.audience,
            issuer="https://kubernetes.default.svc.cluster.local",
            resource_scope=request.resource_scope,
            allowed_operations=frozenset(
                {CredentialOperation.APPLY_PLAN, CredentialOperation.ROLLBACK}
            ),
            enabled=True,
            lifetime_seconds=600,
        )
        broker = broker_factory.for_request(request)

        first = broker.run(
            request,
            lambda token, handle: adapter.run_with_credential(
                plan,
                token,
                handle,
                idempotency_key="phase-4-3-live-attempt",
            ),
        )
        replay = broker.run(
            request,
            lambda token, handle: adapter.run_with_credential(
                plan,
                token,
                handle,
                idempotency_key="phase-4-3-live-attempt",
            ),
        )

    print(
        json.dumps(
            {
                "adapter_id": adapter.adapter_id,
                "artifact_digest": artifact_digest,
                "broker_id": first.handle.broker_id,
                "credential_lifetime_seconds": int(
                    (first.handle.expires_at - first.handle.issued_at).total_seconds()
                ),
                "credential_reference_scheme": first.handle.reference.split(":", 1)[0],
                "first_changed": first.result.execution.changed,
                "first_verified": first.result.verification.passed,
                "plan_digest": plan.digest,
                "replay_changed": replay.result.execution.changed,
                "replay_verified": replay.result.verification.passed,
                "resource_id": adapter.resource_id,
                "revision": first.result.observation.observed_revision,
                "simulated": first.result.execution.simulated,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
