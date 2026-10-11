"""Operator commands for the human-dispatched Devin flow (ADR-0044).

`prepare` advances one workflow through its pre-implementation stages and stops at the
implementation task, so a continuously running worker is not needed (and would race the operator
for that task). `dispatch`, `sync`, and `cancel` call the same service operations as the API.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from control_plane.domain import ControlPlaneError, WorkflowState
from control_plane.routing import DataClassification, RiskLevel
from control_plane.runtime import build_runtime
from control_plane.service import ControlPlaneService

STOP_STATES = {
    WorkflowState.BLOCKED.value,
    WorkflowState.FAILED.value,
    WorkflowState.CANCELLED.value,
}


def add_devin_parser(subparsers: Any) -> None:
    devin = subparsers.add_parser("devin", help="dispatch and ingest Devin implementation sessions")
    commands = devin.add_subparsers(dest="devin_command", required=True)
    prepare = commands.add_parser(
        "prepare", help="create a workflow and stop at its implementation task"
    )
    prepare.add_argument("--title", required=True)
    prepare.add_argument("--objective", required=True)
    prepare.add_argument("--repository-scope", required=True)
    prepare.add_argument("--idempotency-key", required=True)
    prepare.add_argument("--risk", default="MEDIUM", choices=["LOW", "MEDIUM", "HIGH"])
    prepare.add_argument(
        "--data-classification", default="INTERNAL", choices=["PUBLIC", "INTERNAL"]
    )
    prepare.add_argument("--max-stage-leases", type=int, default=8)
    dispatch = commands.add_parser("dispatch", help="send a ready implementation task to Devin")
    dispatch.add_argument("--task-id", required=True)
    dispatch.add_argument("--max-cost-units", type=int, required=True)
    sync = commands.add_parser(
        "sync", help="poll the session and ingest Git-verified evidence when it finishes"
    )
    sync.add_argument("--task-id", required=True)
    cancel = commands.add_parser("cancel", help="terminate the session and close the attempt")
    cancel.add_argument("--task-id", required=True)
    for command in (prepare, dispatch, sync, cancel):
        command.add_argument("--principal", default="dev-operator")


def prepare_for_devin(
    service: ControlPlaneService,
    *,
    principal: str,
    title: str,
    objective: str,
    repository_scope: str,
    idempotency_key: str,
    risk: str,
    data_classification: str,
    max_stage_leases: int,
) -> dict[str, Any]:
    workflow = service.create_workflow(
        requester_id=principal,
        title=title,
        description=objective,
        idempotency_key=idempotency_key,
        risk=RiskLevel[risk],
        data_classification=DataClassification[data_classification],
        repository_scope=repository_scope,
    )
    workflow_id = str(workflow["id"])
    for _ in range(max_stage_leases):
        workflow = service.get_workflow(workflow_id, principal_id=principal)
        if (
            workflow["state"] == WorkflowState.IMPLEMENTING.value
            or workflow["state"] in STOP_STATES
        ):
            break
        task = service.lease_next_task(worker_id="orchestrator", workflow_id=workflow_id)
        if task is None:
            break
        service.execute_leased_task(
            task_id=str(task["id"]),
            lease_token=str(task["lease_token"]),
            worker_id="orchestrator",
        )
    workflow = service.get_workflow(workflow_id, principal_id=principal)
    implement = next(
        (
            item
            for item in workflow["tasks"]
            if item["kind"] == "IMPLEMENT" and item["status"] == "READY"
        ),
        None,
    )
    return {
        "workflow_id": workflow_id,
        "state": workflow["state"],
        "implementation_task_id": implement["id"] if implement else None,
        "ready_for_devin": workflow["state"] == WorkflowState.IMPLEMENTING.value
        and implement is not None,
    }


def run_devin_command(args: argparse.Namespace) -> int:
    runtime = build_runtime(activate_github_app_client=False, activate_oidc_client=False)
    service = runtime.service
    try:
        if args.devin_command == "prepare":
            result = prepare_for_devin(
                service,
                principal=args.principal,
                title=args.title,
                objective=args.objective,
                repository_scope=args.repository_scope,
                idempotency_key=args.idempotency_key,
                risk=args.risk,
                data_classification=args.data_classification,
                max_stage_leases=args.max_stage_leases,
            )
            exit_code = 0 if result["ready_for_devin"] else 2
        elif args.devin_command == "dispatch":
            result = service.dispatch_devin_task(
                task_id=args.task_id,
                principal_id=args.principal,
                max_cost_units=args.max_cost_units,
            )
            exit_code = 0
        elif args.devin_command == "sync":
            result = service.sync_devin_task(task_id=args.task_id, principal_id=args.principal)
            exit_code = 0
        else:
            result = service.cancel_devin_task(task_id=args.task_id, principal_id=args.principal)
            exit_code = 0
    except ControlPlaneError as error:
        raise SystemExit(f"{type(error).__name__}: {error}") from error
    finally:
        runtime.engine.dispose()
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return exit_code
