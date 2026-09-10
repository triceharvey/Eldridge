from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import ssl
from pathlib import Path

import httpx

from control_plane.audit import verify_audit_chain
from control_plane.credentials import (
    CredentialOperation,
    KubectlTokenRequester,
    KubernetesTokenRequestBrokerFactory,
)
from control_plane.deployment import EnvironmentClassification
from control_plane.domain import ApprovalAction, ApprovalDecision, WorkflowState
from control_plane.kubernetes_deployment import LocalK3dConfigMapAdapter
from control_plane.persistence import (
    Approval,
    Workflow,
    initialize_database,
    make_engine,
    make_session_factory,
    seed_principals,
)
from control_plane.service import ControlPlaneService


class VerificationFaultClient:
    """Corrupts the first confirmed update before the adapter's independent read."""

    def __init__(self, client: httpx.Client) -> None:
        self.client = client
        self.put_calls = 0
        self.injected = False

    def get(self, url: str, **kwargs: object) -> httpx.Response:
        return self.client.get(url, **kwargs)  # type: ignore[arg-type]

    def put(self, url: str, **kwargs: object) -> httpx.Response:
        self.put_calls += 1
        response = self.client.put(url, **kwargs)  # type: ignore[arg-type]
        if self.put_calls == 1:
            payload = response.json()
            payload["data"]["revision"] = "0" * 40
            injected = self.client.put(url, headers=kwargs.get("headers"), json=payload)
            if injected.status_code != 200:
                raise RuntimeError("controlled verification-fault injection was rejected")
            self.injected = True
        return response


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
    return servers[0], ssl.create_default_context(cadata=authority)


def main() -> None:
    parser = argparse.ArgumentParser(description="Exercise controlled local k3d recovery")
    parser.add_argument("--kubeconfig", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--revision", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.revision):
        raise SystemExit("validation revision must be a lowercase Git SHA-1")

    artifact_digest = f"sha256:{hashlib.sha256(args.manifest.read_bytes()).hexdigest()}"
    server, context = _cluster_connection(args.kubeconfig)
    engine = make_engine("sqlite:///:memory:")
    initialize_database(engine)
    session_factory = make_session_factory(engine)
    with session_factory() as session:
        seed_principals(session)

    try:
        with httpx.Client(
            base_url=server,
            verify=context,
            timeout=10,
            trust_env=False,
        ) as raw_client:
            client = VerificationFaultClient(raw_client)
            adapter = LocalK3dConfigMapAdapter(client=client, api_server=server)
            broker_factory = KubernetesTokenRequestBrokerFactory(
                requester=KubectlTokenRequester(kubeconfig=args.kubeconfig),
                environment_id=adapter.environment_id,
                adapter_id=adapter.adapter_id,
                namespace=adapter.namespace,
                service_account="deployment-worker",
                audience=adapter.audience,
                issuer="https://kubernetes.default.svc.cluster.local",
                resource_scope=("namespace/eldridge-validation/configmap/eldridge-release",),
                allowed_operations=frozenset(
                    {CredentialOperation.APPLY_PLAN, CredentialOperation.ROLLBACK}
                ),
                enabled=True,
                lifetime_seconds=600,
            )
            service = ControlPlaneService(
                session_factory,
                deployment_adapters=(adapter,),
                credential_broker_factory=broker_factory,
                enable_local_deployment=True,
            )
            workflow = service.create_workflow(
                requester_id="dev-operator",
                title="Phase 4.4 controlled recovery",
                description="Inject failed verification and restore the prior release marker.",
                idempotency_key="phase-4-4-live-workflow",
                repository_scope="triceharvey/Eldridge",
            )
            with session_factory() as session, session.begin():
                stored = session.get(Workflow, workflow["id"])
                if stored is None:
                    raise RuntimeError("validation workflow was not stored")
                stored.state = WorkflowState.MERGED.value
                stored.candidate_revision = args.revision
                stored.merged_revision = args.revision

            service.register_deployment_environment(
                actor_id="dev-operator",
                environment_id=adapter.environment_id,
                name="Ephemeral local k3d recovery",
                classification=EnvironmentClassification.DEVELOPMENT,
                repository="triceharvey/Eldridge",
                base_branch="main",
                resource_scope=(adapter.resource_id,),
                required_checks=(),
                required_attestations=(),
                verification_policy=adapter.verification_probes,
                rollback_policy=adapter.rollback_reference,
                policy_version=adapter.policy_version,
                provider="local-k3d",
                account_scope="local",
                region="local",
                adapter_id=adapter.adapter_id,
            )
            plan = service.create_deployment_plan(
                workflow_id=workflow["id"],
                actor_id="dev-operator",
                environment_id=adapter.environment_id,
                artifact_digests=(artifact_digest,),
                operations=(
                    {
                        "kind": "UPDATE_SERVICE",
                        "resource_id": adapter.resource_id,
                        "artifact_digest": artifact_digest,
                    },
                ),
                declared_impact="Update one local release marker, inject failure, then recover.",
                verification_probes=adapter.verification_probes,
                rollback_reference=adapter.rollback_reference,
                idempotency_key="phase-4-4-live-plan",
            )
            service.approve(
                workflow_id=workflow["id"],
                approver_id="dev-operator",
                action=ApprovalAction.DEPLOY,
                target=adapter.environment_id,
                revision=args.revision,
                decision=ApprovalDecision.APPROVED,
                rationale="Authorize the exact local fault-injection deployment plan.",
                environment_id=adapter.environment_id,
                plan_digest=plan["digest"],
            )
            failed_attempt = service.execute_local_deployment(
                workflow_id=workflow["id"],
                plan_id=plan["id"],
                actor_id="dev-operator",
                idempotency_key="phase-4-4-live-failed-verification",
            )
            failed_state = service.get_workflow(workflow["id"], principal_id="dev-operator")[
                "state"
            ]
            rollback_approval = service.approve(
                workflow_id=workflow["id"],
                approver_id="dev-operator",
                action=ApprovalAction.ROLLBACK,
                target=adapter.rollback_reference,
                revision=args.revision,
                decision=ApprovalDecision.APPROVED,
                rationale="Restore the exact recorded pre-deployment snapshot.",
                environment_id=adapter.environment_id,
                plan_digest=plan["digest"],
                deployment_attempt_id=failed_attempt["id"],
            )
            rollback = service.execute_local_rollback(
                workflow_id=workflow["id"],
                attempt_id=failed_attempt["id"],
                actor_id="dev-operator",
                idempotency_key="phase-4-4-live-rollback",
            )
            final_state = service.get_workflow(workflow["id"], principal_id="dev-operator")["state"]
            with session_factory() as session:
                audit_valid = verify_audit_chain(session, workflow["id"])
                stored_approval = session.get(Approval, rollback_approval["id"])
                rollback_approval_consumed = (
                    stored_approval is not None and stored_approval.consumed_at is not None
                )

        print(
            json.dumps(
                {
                    "adapter_id": adapter.adapter_id,
                    "audit_chain_valid": audit_valid,
                    "credential_lifetime_seconds": 600,
                    "failed_attempt_status": failed_attempt["status"],
                    "failed_state": failed_state,
                    "fault_injected": client.injected,
                    "final_state": final_state,
                    "plan_digest": plan["digest"],
                    "recovery_duration_ms": rollback["recovery_duration_ms"],
                    "rollback_approval_consumed": rollback_approval_consumed,
                    "rollback_changed": rollback["evidence"]["changed"],
                    "rollback_operation": CredentialOperation.ROLLBACK.value,
                    "rollback_status": rollback["status"],
                    "rollback_verified": rollback["evidence"]["verification"][
                        "restored_snapshot_matches"
                    ],
                    "simulated": False,
                },
                sort_keys=True,
            )
        )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
