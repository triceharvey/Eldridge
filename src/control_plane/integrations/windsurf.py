from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256


@dataclass(frozen=True)
class WindsurfHandoffArtifact:
    workflow_id: str
    task_id: str
    repository: str
    branch: str
    base_revision: str
    objective: str
    assigned_principal: str
    policy_version: str
    writable_paths: tuple[str, ...]
    required_evidence: tuple[str, ...]
    integration_mode: str
    digest: str


class WindsurfHandoff:
    """Creates a revision-bound handoff for Cascade/MCP or human-operated IDE use."""

    def create(
        self,
        *,
        workflow_id: str,
        task_id: str,
        repository: str,
        branch: str,
        base_revision: str,
        objective: str,
        assigned_principal: str,
        policy_version: str,
        writable_paths: tuple[str, ...],
    ) -> WindsurfHandoffArtifact:
        payload = {
            "workflow_id": workflow_id,
            "task_id": task_id,
            "repository": repository,
            "branch": branch,
            "base_revision": base_revision,
            "objective": objective,
            "assigned_principal": assigned_principal,
            "policy_version": policy_version,
            "writable_paths": list(writable_paths),
            "required_evidence": [
                "result revision",
                "files changed",
                "test results",
                "tool activity summary",
            ],
            "integration_mode": "git-and-mcp-handoff",
        }
        digest = sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return WindsurfHandoffArtifact(
            workflow_id=workflow_id,
            task_id=task_id,
            repository=repository,
            branch=branch,
            base_revision=base_revision,
            objective=objective,
            assigned_principal=assigned_principal,
            policy_version=policy_version,
            writable_paths=writable_paths,
            required_evidence=tuple(payload["required_evidence"]),
            integration_mode="git-and-mcp-handoff",
            digest=digest,
        )
