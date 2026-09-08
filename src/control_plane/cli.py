from __future__ import annotations

import argparse
import json
from uuid import uuid4

from control_plane.config import Settings
from control_plane.domain import ApprovalAction, ApprovalDecision, WorkflowState
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
    args = parser.parse_args()
    if args.command == "demo":
        raise SystemExit(run_demo(args.database_url))


if __name__ == "__main__":
    main()
