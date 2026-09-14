from __future__ import annotations

import argparse
import json
from pathlib import Path
from uuid import uuid4

from control_plane.config import Settings
from control_plane.domain import ApprovalAction, ApprovalDecision, WorkflowState
from control_plane.operator_workflow import (
    OperatorWorkflowManifest,
    build_operator_runtime,
    drive_operator_workflow,
    preflight_operator_workflow,
)
from control_plane.production_readiness import evaluate_production_readiness_file
from control_plane.runtime import build_runtime


def run_demo(database_url: str) -> int:
    settings = Settings(database_url=database_url, environment="development")
    runtime = build_runtime(settings, create_schema=True)
    service = runtime.service
    workflow = service.create_workflow(
        requester_id="dev-operator",
        title="Phase 1 deterministic demonstration",
        description="Exercise every required mock-agent stage without external calls or commands.",
        idempotency_key=f"demo-{uuid4()}",
    )
    print(json.dumps({"event": "workflow_created", "workflow": workflow}, indent=2))
    while True:
        workflow = service.get_workflow(workflow["id"], principal_id="dev-operator")
        if workflow["state"] == WorkflowState.AWAITING_HUMAN_APPROVAL.value:
            break
        task = service.lease_next_task(worker_id="orchestrator")
        if task is None:
            raise RuntimeError("workflow has no ready task before approval gate")
        service.execute_leased_task(
            task_id=task["id"],
            lease_token=task["lease_token"],
            worker_id="orchestrator",
        )
        print(json.dumps({"event": "task_completed", "kind": task["kind"]}))
    print(json.dumps({"event": "human_gate_reached", "workflow": workflow}, indent=2))
    approval = service.approve(
        workflow_id=workflow["id"],
        approver_id="dev-operator",
        action=ApprovalAction.MERGE,
        target="example/repository:protected-main",
        revision=workflow["candidate_revision"],
        decision=ApprovalDecision.APPROVED,
        rationale="Phase 1 demonstration approval only; no merge operation is implemented.",
    )
    final = service.get_workflow(workflow["id"], principal_id="dev-operator")
    print(json.dumps({"event": "approval_recorded", "approval": approval}, indent=2))
    print(json.dumps({"event": "workflow_complete", "workflow": final}, indent=2))
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="AI engineering control-plane CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)
    demo = subparsers.add_parser("demo", help="run the deterministic Phase 1 workflow")
    demo.add_argument(
        "--database-url",
        default="sqlite:///:memory:",
        help="SQLAlchemy database URL; defaults to an ephemeral local demo database",
    )
    production = subparsers.add_parser("production", help="evaluate hosted production evidence")
    production_subparsers = production.add_subparsers(dest="production_command", required=True)
    readiness = production_subparsers.add_parser(
        "readiness", help="fail closed on missing, invalid, stale, or unbound evidence"
    )
    readiness.add_argument("--manifest", type=Path, required=True)
    workflow = subparsers.add_parser("workflow", help="preflight or run a governed workflow")
    workflow_subparsers = workflow.add_subparsers(dest="workflow_command", required=True)
    for command in ("preflight", "run"):
        command_parser = workflow_subparsers.add_parser(command)
        command_parser.add_argument("--manifest", type=Path, required=True)
        command_parser.add_argument("--repository-registry", type=Path, required=True)
        command_parser.add_argument("--provider-policy", type=Path, required=True)
        if command == "run":
            command_parser.add_argument("--database-url", required=True)
            command_parser.add_argument(
                "--worktree-root", type=Path, default=Path(".control-plane-worktrees")
            )
            command_parser.add_argument("--create-schema", action="store_true")
            command_parser.add_argument("--confirm-execution", action="store_true")
            command_parser.add_argument("--confirm-external-egress", action="store_true")
    args = parser.parse_args()
    if args.command == "demo":
        raise SystemExit(run_demo(args.database_url))
    if args.command == "production":
        try:
            report = evaluate_production_readiness_file(args.manifest)
        except (OSError, ValueError) as error:
            raise SystemExit(str(error)) from error
        print(json.dumps(report, indent=2, sort_keys=True))
        raise SystemExit(0 if report["ready"] else 2)
    if args.command == "workflow":
        manifest = OperatorWorkflowManifest.from_file(args.manifest)
        preflight = preflight_operator_workflow(
            manifest,
            repository_registry_file=args.repository_registry,
            provider_policy_file=args.provider_policy,
        )
        if args.workflow_command == "preflight":
            print(json.dumps(preflight, indent=2, sort_keys=True))
            raise SystemExit(0)
        if not args.confirm_execution:
            raise SystemExit("workflow execution requires --confirm-execution")
        if preflight["requires_external_egress_confirmation"] and not args.confirm_external_egress:
            raise SystemExit("external provider use requires --confirm-external-egress")
        runtime = build_operator_runtime(
            manifest,
            repository_registry_file=args.repository_registry,
            provider_policy_file=args.provider_policy,
            database_url=args.database_url,
            worktree_root=args.worktree_root,
            create_schema=args.create_schema,
        )
        try:
            final = drive_operator_workflow(
                runtime.service,
                manifest,
                emit=lambda event: print(json.dumps(event, sort_keys=True)),
            )
            print(json.dumps({"event": "workflow_stopped", "workflow": final}, indent=2))
        finally:
            runtime.engine.dispose()


if __name__ == "__main__":
    main()
