from __future__ import annotations

import argparse
import json
from pathlib import Path
from uuid import uuid4

from sqlalchemy.engine import make_url

from control_plane.audit_export import (
    build_audit_export,
    build_provider_usage_report,
    verify_audit_export,
)
from control_plane.config import Settings
from control_plane.domain import ApprovalAction, ApprovalDecision, WorkflowState
from control_plane.operator_workflow import (
    OperatorWorkflowManifest,
    build_operator_runtime,
    drive_operator_workflow,
    preflight_operator_workflow,
)
from control_plane.persistence import make_engine, make_session_factory
from control_plane.production_readiness import evaluate_production_readiness_file
from control_plane.runtime import build_runtime
from control_plane.video_trial import (
    RunwayJobEvidence,
    VideoCandidateManifest,
    VideoReviewerAssessment,
    create_runway_job_evidence,
    inspect_video_candidate,
)
from control_plane.visual_evidence import VisualEvidenceManifest, inspect_visual_evidence
from control_plane.visual_review import (
    VisualCandidateAssessment,
    VisualReviewAssessment,
    inspect_visual_candidate_review,
    inspect_visual_review,
)


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
    audit = subparsers.add_parser("audit", help="export or verify portable workflow evidence")
    audit_subparsers = audit.add_subparsers(dest="audit_command", required=True)
    audit_export = audit_subparsers.add_parser("export")
    audit_export.add_argument("--database-url", required=True)
    audit_export.add_argument("--workflow-id", required=True)
    audit_export.add_argument("--output", type=Path, required=True)
    audit_verify = audit_subparsers.add_parser("verify")
    audit_verify.add_argument("--bundle", type=Path, required=True)
    provider_usage = audit_subparsers.add_parser(
        "provider-usage", help="summarize recorded provider attempts without model content"
    )
    provider_usage.add_argument("--database-url", required=True)
    provider_usage.add_argument("--workflow-id")
    provider_usage.add_argument("--provider-id")
    visual_evidence = audit_subparsers.add_parser(
        "visual-evidence", help="verify local images against a pinned repository revision"
    )
    visual_evidence.add_argument("--manifest", type=Path, required=True)
    visual_evidence.add_argument("--repository-registry", type=Path, required=True)
    visual_review = audit_subparsers.add_parser(
        "visual-review", help="bind a proposed retry critique to verified local image evidence"
    )
    visual_review.add_argument("--manifest", type=Path, required=True)
    visual_review.add_argument("--assessment", type=Path, required=True)
    visual_review.add_argument("--repository-registry", type=Path, required=True)
    candidate_review = audit_subparsers.add_parser(
        "visual-candidate-review",
        help="bind seven reviewer-reported quality checks to a pending image candidate",
    )
    candidate_review.add_argument("--manifest", type=Path, required=True)
    candidate_review.add_argument("--assessment", type=Path, required=True)
    candidate_review.add_argument("--repository-registry", type=Path, required=True)
    video_candidate = audit_subparsers.add_parser(
        "video-candidate", help="bind a local video and sampled review to an approved still"
    )
    video_candidate.add_argument("--source-manifest", type=Path, required=True)
    video_candidate.add_argument("--manifest", type=Path, required=True)
    video_candidate.add_argument("--assessment", type=Path, required=True)
    video_candidate.add_argument("--repository-registry", type=Path, required=True)
    video_candidate.add_argument("--verify-frames", action="store_true")
    video_candidate.add_argument("--runway-job-evidence", type=Path)
    capture = subparsers.add_parser("capture", help="package already-saved local evidence")
    capture_subparsers = capture.add_subparsers(dest="capture_command", required=True)
    runway_receipt = capture_subparsers.add_parser(
        "runway-receipt", help="hash Runway browser captures for later candidate audit"
    )
    runway_receipt.add_argument("--manifest", type=Path, required=True)
    runway_receipt.add_argument("--repository-registry", type=Path, required=True)
    runway_receipt.add_argument("--model-name", required=True)
    runway_receipt.add_argument("--job-details", required=True)
    runway_receipt.add_argument("--generation-settings", required=True)
    runway_receipt.add_argument("--credit-ledger", required=True)
    runway_receipt.add_argument("--credit-balance-before")
    runway_receipt.add_argument("--download-record")
    runway_receipt.add_argument("--output", type=Path, required=True)
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
    if args.command == "capture" and args.capture_command == "runway-receipt":
        paths = {
            "JOB_DETAILS": args.job_details,
            "GENERATION_SETTINGS": args.generation_settings,
            "CREDIT_LEDGER": args.credit_ledger,
        }
        if args.download_record is not None:
            paths["DOWNLOAD_RECORD"] = args.download_record
        if args.credit_balance_before is not None:
            paths["CREDIT_BALANCE_BEFORE"] = args.credit_balance_before
        try:
            receipt = create_runway_job_evidence(
                VideoCandidateManifest.from_file(args.manifest),
                repository_registry_file=args.repository_registry,
                model_name_claim=args.model_name,
                capture_paths=paths,
                output=args.output,
            )
        except (OSError, ValueError) as error:
            raise SystemExit(str(error)) from error
        print(json.dumps(receipt.model_dump(), indent=2, sort_keys=True))
        raise SystemExit(0)
    if args.command == "audit":
        if args.audit_command == "video-candidate":
            try:
                report = inspect_video_candidate(
                    VideoCandidateManifest.from_file(args.manifest),
                    VideoReviewerAssessment.from_file(args.assessment),
                    source_evidence_manifest=VisualEvidenceManifest.from_file(args.source_manifest),
                    repository_registry_file=args.repository_registry,
                    verify_frames=args.verify_frames,
                    runway_job_evidence=(
                        RunwayJobEvidence.from_file(args.runway_job_evidence)
                        if args.runway_job_evidence is not None
                        else None
                    ),
                )
            except (OSError, ValueError) as error:
                raise SystemExit(str(error)) from error
            print(json.dumps(report, indent=2, sort_keys=True))
            raise SystemExit(0)
        if args.audit_command == "visual-candidate-review":
            try:
                report = inspect_visual_candidate_review(
                    VisualCandidateAssessment.from_file(args.assessment),
                    evidence_manifest=VisualEvidenceManifest.from_file(args.manifest),
                    repository_registry_file=args.repository_registry,
                )
            except (OSError, ValueError) as error:
                raise SystemExit(str(error)) from error
            print(json.dumps(report, indent=2, sort_keys=True))
            raise SystemExit(0)
        if args.audit_command == "visual-review":
            try:
                report = inspect_visual_review(
                    VisualReviewAssessment.from_file(args.assessment),
                    evidence_manifest=VisualEvidenceManifest.from_file(args.manifest),
                    repository_registry_file=args.repository_registry,
                )
            except (OSError, ValueError) as error:
                raise SystemExit(str(error)) from error
            print(json.dumps(report, indent=2, sort_keys=True))
            raise SystemExit(0)
        if args.audit_command == "visual-evidence":
            try:
                visual_manifest = VisualEvidenceManifest.from_file(args.manifest)
                report = inspect_visual_evidence(
                    visual_manifest, repository_registry_file=args.repository_registry
                )
            except (OSError, ValueError) as error:
                raise SystemExit(str(error)) from error
            print(json.dumps(report, indent=2, sort_keys=True))
            raise SystemExit(0)
        if args.audit_command == "provider-usage":
            database = make_url(args.database_url)
            if (
                database.drivername.startswith("sqlite")
                and database.database is not None
                and database.database != ":memory:"
                and not Path(database.database).is_file()
            ):
                raise SystemExit("provider usage database does not exist")
            engine = make_engine(args.database_url)
            try:
                with make_session_factory(engine)() as session:
                    report = build_provider_usage_report(
                        session,
                        workflow_id=args.workflow_id,
                        provider_id=args.provider_id,
                    )
            except (OSError, ValueError) as error:
                raise SystemExit(str(error)) from error
            finally:
                engine.dispose()
            print(json.dumps(report, indent=2, sort_keys=True))
            raise SystemExit(0)
        if args.audit_command == "verify":
            try:
                payload = json.loads(args.bundle.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as error:
                raise SystemExit("audit bundle is unreadable or invalid JSON") from error
            valid = verify_audit_export(payload)
            print(
                json.dumps(
                    {"bundle": str(args.bundle), "valid": valid},
                    indent=2,
                    sort_keys=True,
                )
            )
            raise SystemExit(0 if valid else 2)
        if args.output.exists():
            raise SystemExit("audit export output already exists")
        if not args.output.parent.is_dir():
            raise SystemExit("audit export parent directory does not exist")
        engine = make_engine(args.database_url)
        try:
            with make_session_factory(engine)() as session:
                bundle = build_audit_export(session, args.workflow_id)
            args.output.write_text(
                json.dumps(bundle, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        except (OSError, ValueError) as error:
            raise SystemExit(str(error)) from error
        finally:
            engine.dispose()
        print(
            json.dumps(
                {
                    "bundle_digest": bundle["bundle_digest"],
                    "output": str(args.output),
                    "workflow_id": args.workflow_id,
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise SystemExit(0)
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
