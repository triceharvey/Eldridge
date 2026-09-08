from __future__ import annotations

from hashlib import sha256

from control_plane.domain import ProviderRequest, ProviderResult, TaskKind


class MockProvider:
    """Deterministic provider used to prove orchestration without external model access."""

    def __init__(self, *, fail_for: frozenset[TaskKind] = frozenset()) -> None:
        self.fail_for = fail_for

    @property
    def name(self) -> str:
        return "mock"

    def capabilities(self) -> frozenset[str]:
        return frozenset(
            {
                "reasoning",
                "code_generation",
                "review",
                "structured_output",
            }
        )

    def submit(self, request: ProviderRequest) -> ProviderResult:
        if request.task_kind in self.fail_for:
            raise TimeoutError(f"deterministic failure for {request.task_kind.value}")
        digest = sha256(
            f"{request.workflow_id}:{request.task_kind.value}:{request.objective}".encode()
        ).hexdigest()
        output = self._output_for(request, digest)
        return ProviderResult(
            status="SUCCEEDED",
            output=output,
            provider=self.name,
            model="deterministic-mock-v1",
            usage={"input_tokens": 0, "output_tokens": 0},
        )

    def cancel(self, run_id: str) -> bool:
        return True

    def health(self) -> bool:
        return True

    def _output_for(self, request: ProviderRequest, digest: str) -> dict[str, object]:
        common: dict[str, object] = {
            "schema_version": "1",
            "task_kind": request.task_kind.value,
            "deterministic": True,
        }
        if request.task_kind == TaskKind.PLAN:
            return common | {
                "plan": [
                    "implement the assigned change in an isolated workspace",
                    "run deterministic tests and security review",
                    "request human approval for the exact revision",
                ],
                "assumptions": ["mock execution only in Phase 1"],
            }
        if request.task_kind == TaskKind.ARCHITECTURE_REVIEW:
            return common | {
                "findings": [],
                "disposition": "no blocking findings in deterministic fixture",
                "independent_review": True,
            }
        if request.task_kind == TaskKind.IMPLEMENT:
            return common | {
                "change_summary": "deterministic mock change artifact",
                "candidate_revision": f"mock-{digest[:40]}",
                "commands_requested": [],
            }
        if request.task_kind == TaskKind.TEST:
            return common | {"tests_passed": True, "test_count": 1, "failures": []}
        if request.task_kind == TaskKind.SECURITY_REVIEW:
            return common | {"policy_passed": True, "findings": []}
        return common | {
            "review_passed": True,
            "blocking_findings": [],
            "recommendation": "request human merge approval",
        }
