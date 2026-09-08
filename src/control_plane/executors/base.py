from typing import Protocol

from control_plane.domain import ExecutionContext, ExecutionResult, ProviderResult, TaskKind


class TaskExecutor(Protocol):
    @property
    def name(self) -> str: ...

    def execute(
        self,
        task_kind: TaskKind,
        provider_result: ProviderResult,
        context: ExecutionContext | None = None,
    ) -> ExecutionResult: ...
