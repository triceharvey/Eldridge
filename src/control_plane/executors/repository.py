from __future__ import annotations

from collections.abc import Callable

from control_plane.domain import (
    ExecutionContext,
    ExecutionResult,
    ProviderResult,
    SandboxError,
    TaskKind,
)
from control_plane.executors.base import TaskExecutor
from control_plane.executors.docker import (
    DockerSandboxExecutor,
    DockerSandboxPolicy,
    SandboxResult,
)
from control_plane.tools import ToolRequest
from control_plane.workspaces import GitWorktreeManager, RepositoryRegistry

SandboxFactory = Callable[[DockerSandboxPolicy], DockerSandboxExecutor]


class IsolatedRepositoryExecutor:
    """Composes registered Git worktrees with the typed, networkless Docker sandbox."""

    def __init__(
        self,
        *,
        registry: RepositoryRegistry,
        worktrees: GitWorktreeManager,
        fallback: TaskExecutor,
        sandbox_factory: SandboxFactory = DockerSandboxExecutor,
        require_tool_evidence: bool = False,
    ) -> None:
        self.registry = registry
        self.worktrees = worktrees
        self.fallback = fallback
        self.sandbox_factory = sandbox_factory
        self.require_tool_evidence = require_tool_evidence

    @property
    def name(self) -> str:
        return "isolated-repository-executor"

    def execute(
        self,
        task_kind: TaskKind,
        provider_result: ProviderResult,
        context: ExecutionContext | None = None,
    ) -> ExecutionResult:
        if (
            context is None
            or context.repository_scope is None
            or task_kind in {TaskKind.PLAN, TaskKind.ARCHITECTURE_REVIEW}
        ):
            return self.fallback.execute(task_kind, provider_result, context)

        registration = self.registry.resolve(context.repository_scope)
        base_revision = context.candidate_revision or registration.base_revision
        workspace = self.worktrees.create(
            repository=registration.path,
            base_revision=base_revision,
            task_id=context.task_id,
        )
        succeeded = False
        try:
            requests = self._tool_requests(provider_result)
            if self.require_tool_evidence and task_kind is TaskKind.TEST and not requests:
                raise SandboxError("test task requires typed execution evidence")
            writable_paths = registration.writable_paths if task_kind is TaskKind.IMPLEMENT else ()
            sandbox = self.sandbox_factory(DockerSandboxPolicy(writable_paths=writable_paths))
            results = tuple(
                sandbox.execute(workspace=workspace.path, request=request) for request in requests
            )
            failed = next((result for result in results if result.status != "SUCCEEDED"), None)
            if failed is not None:
                raise SandboxError(
                    f"typed tool {failed.tool.value} failed with exit code {failed.exit_code}"
                )
            result_revision = self.worktrees.commit(workspace)
            changed_files = self.worktrees.changed_files(workspace, result_revision)
            if task_kind is not TaskKind.IMPLEMENT and changed_files:
                raise SandboxError("non-implementation task modified the repository")
            succeeded = True
            return ExecutionResult(
                status="SUCCEEDED",
                summary=f"isolated repository execution completed for {task_kind.value}",
                evidence={
                    "executor": self.name,
                    "repository_scope": registration.scope_id,
                    "branch": workspace.branch,
                    "base_revision": workspace.base_revision,
                    "result_revision": result_revision,
                    "changed_files": list(changed_files),
                    "containment_required": context.containment_required,
                    "sandbox": [self._sandbox_evidence(result) for result in results],
                },
                commands_executed=tuple(request.name.value for request in requests),
            )
        finally:
            self.worktrees.remove(
                workspace,
                delete_branch=task_kind is not TaskKind.IMPLEMENT or not succeeded,
            )

    @staticmethod
    def _tool_requests(provider_result: ProviderResult) -> tuple[ToolRequest, ...]:
        raw_requests = provider_result.output.get("tool_requests", [])
        if not isinstance(raw_requests, list):
            raise SandboxError("provider tool_requests must be a list")
        requests: list[ToolRequest] = []
        for raw in raw_requests:
            if not isinstance(raw, dict):
                raise SandboxError("provider tool request must be an object")
            payload = raw.get("input", raw)
            if not isinstance(payload, dict):
                raise SandboxError("provider tool request input must be an object")
            outer_name = raw.get("name")
            if outer_name is not None and outer_name != payload.get("name"):
                raise SandboxError("provider tool request names do not match")
            try:
                requests.append(ToolRequest.model_validate(payload))
            except ValueError as exc:
                raise SandboxError("provider proposed an invalid typed tool request") from exc
        return tuple(requests)

    @staticmethod
    def _sandbox_evidence(result: SandboxResult) -> dict[str, object]:
        return {
            "tool": result.tool.value,
            "exit_code": result.exit_code,
            "network": result.network,
            "read_only_root": result.read_only_root,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
