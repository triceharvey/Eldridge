from __future__ import annotations

import copy
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select

from control_plane.audit_export import build_audit_export, verify_audit_export
from control_plane.cli import main
from control_plane.persistence import (
    AuditEvent,
    initialize_database,
    make_engine,
    make_session_factory,
    seed_principals,
)
from control_plane.service import ControlPlaneService


def _workflow(service: ControlPlaneService) -> dict[str, object]:
    return service.create_workflow(
        requester_id="dev-operator",
        title="Portable project evidence",
        description="Exercise project-bound audit and quality evidence export.",
        idempotency_key="portable-audit-export",
        repository_scope="kaiju-frenchies/local",
    )


def _nested_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {key for item in value.values() for key in _nested_keys(item)}
    if isinstance(value, list):
        return {key for item in value for key in _nested_keys(item)}
    return set()


def test_builds_verifiable_project_bound_bundle(service: ControlPlaneService) -> None:
    workflow = _workflow(service)
    with service.session_factory() as session:
        bundle = build_audit_export(
            session,
            str(workflow["id"]),
            exported_at=datetime(2026, 9, 27, tzinfo=UTC),
        )

    assert bundle["workflow"]["repository_scope"] == "kaiju-frenchies/local"
    assert bundle["audit_chain"]["event_count"] >= 1
    assert bundle["quality_evidence"]["tasks"][0]["kind"] == "PLAN"
    assert bundle["bundle_digest"].startswith("sha256:")
    assert verify_audit_export(bundle)


def test_bundle_omits_model_output_and_lease_material(service: ControlPlaneService) -> None:
    workflow = _workflow(service)
    with service.session_factory() as session:
        bundle = build_audit_export(session, str(workflow["id"]))

    keys = _nested_keys(bundle)
    assert "lease_token" not in keys
    assert "output" not in keys
    assert "metadata" not in keys


def test_bundle_tampering_is_detected(service: ControlPlaneService) -> None:
    workflow = _workflow(service)
    with service.session_factory() as session:
        bundle = build_audit_export(session, str(workflow["id"]))

    tampered = copy.deepcopy(bundle)
    tampered["workflow"]["repository_scope"] = "other/project"
    assert not verify_audit_export(tampered)


def test_export_refuses_tampered_database_chain(service: ControlPlaneService) -> None:
    workflow = _workflow(service)
    with service.session_factory() as session, session.begin():
        event = session.scalar(
            select(AuditEvent)
            .where(AuditEvent.workflow_id == workflow["id"])
            .order_by(AuditEvent.sequence)
        )
        assert event is not None
        event.payload = {"tampered": True}

    with (
        service.session_factory() as session,
        pytest.raises(ValueError, match="audit chain failed verification"),
    ):
        build_audit_export(session, str(workflow["id"]))


def test_export_rejects_unknown_workflow(service: ControlPlaneService) -> None:
    with (
        service.session_factory() as session,
        pytest.raises(ValueError, match="workflow was not found"),
    ):
        build_audit_export(session, "missing-workflow")


def test_cli_exports_and_verifies_bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database_path = tmp_path / "control-plane.db"
    database_url = f"sqlite:///{database_path}"
    engine = make_engine(database_url)
    initialize_database(engine)
    session_factory = make_session_factory(engine)
    with session_factory() as session:
        seed_principals(session)
    service = ControlPlaneService(session_factory)
    workflow = _workflow(service)
    engine.dispose()

    bundle_path = tmp_path / "audit.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "control-plane",
            "audit",
            "export",
            "--database-url",
            database_url,
            "--workflow-id",
            str(workflow["id"]),
            "--output",
            str(bundle_path),
        ],
    )
    with pytest.raises(SystemExit) as export_exit:
        main()
    assert export_exit.value.code == 0
    assert verify_audit_export(json.loads(bundle_path.read_text(encoding="utf-8")))

    monkeypatch.setattr(
        sys,
        "argv",
        ["control-plane", "audit", "verify", "--bundle", str(bundle_path)],
    )
    with pytest.raises(SystemExit) as verify_exit:
        main()
    assert verify_exit.value.code == 0
