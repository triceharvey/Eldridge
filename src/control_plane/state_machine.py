from control_plane.domain import InvalidTransitionError, WorkflowState

ALLOWED_TRANSITIONS: dict[WorkflowState, frozenset[WorkflowState]] = {
    WorkflowState.CREATED: frozenset(
        {WorkflowState.PLANNING, WorkflowState.BLOCKED, WorkflowState.CANCELLED}
    ),
    WorkflowState.PLANNING: frozenset(
        {
            WorkflowState.ARCHITECTURE_REVIEW,
            WorkflowState.BLOCKED,
            WorkflowState.FAILED,
            WorkflowState.CANCELLED,
        }
    ),
    WorkflowState.ARCHITECTURE_REVIEW: frozenset(
        {
            WorkflowState.APPROVED_FOR_IMPLEMENTATION,
            WorkflowState.BLOCKED,
            WorkflowState.FAILED,
            WorkflowState.CANCELLED,
        }
    ),
    WorkflowState.APPROVED_FOR_IMPLEMENTATION: frozenset(
        {WorkflowState.IMPLEMENTING, WorkflowState.CANCELLED}
    ),
    WorkflowState.IMPLEMENTING: frozenset(
        {
            WorkflowState.TESTING,
            WorkflowState.BLOCKED,
            WorkflowState.FAILED,
            WorkflowState.CANCELLED,
        }
    ),
    WorkflowState.TESTING: frozenset(
        {
            WorkflowState.SECURITY_REVIEW,
            WorkflowState.IMPLEMENTING,
            WorkflowState.BLOCKED,
            WorkflowState.FAILED,
            WorkflowState.CANCELLED,
        }
    ),
    WorkflowState.SECURITY_REVIEW: frozenset(
        {
            WorkflowState.CODE_REVIEW,
            WorkflowState.IMPLEMENTING,
            WorkflowState.BLOCKED,
            WorkflowState.FAILED,
            WorkflowState.CANCELLED,
        }
    ),
    WorkflowState.CODE_REVIEW: frozenset(
        {
            WorkflowState.AWAITING_HUMAN_APPROVAL,
            WorkflowState.IMPLEMENTING,
            WorkflowState.BLOCKED,
            WorkflowState.FAILED,
            WorkflowState.CANCELLED,
        }
    ),
    WorkflowState.AWAITING_HUMAN_APPROVAL: frozenset(
        {WorkflowState.APPROVED, WorkflowState.REJECTED, WorkflowState.CANCELLED}
    ),
    WorkflowState.APPROVED: frozenset({WorkflowState.MERGED, WorkflowState.CANCELLED}),
    WorkflowState.MERGED: frozenset({WorkflowState.AWAITING_DEPLOYMENT_APPROVAL}),
    WorkflowState.AWAITING_DEPLOYMENT_APPROVAL: frozenset(
        {
            WorkflowState.DEPLOYED,
            WorkflowState.REJECTED,
            WorkflowState.ROLLBACK_REQUIRED,
        }
    ),
    WorkflowState.BLOCKED: frozenset(
        {
            WorkflowState.PLANNING,
            WorkflowState.ARCHITECTURE_REVIEW,
            WorkflowState.IMPLEMENTING,
            WorkflowState.TESTING,
            WorkflowState.SECURITY_REVIEW,
            WorkflowState.CODE_REVIEW,
            WorkflowState.FAILED,
            WorkflowState.REJECTED,
            WorkflowState.CANCELLED,
        }
    ),
    WorkflowState.DEPLOYED: frozenset({WorkflowState.ROLLBACK_REQUIRED}),
    WorkflowState.FAILED: frozenset(),
    WorkflowState.REJECTED: frozenset(),
    WorkflowState.CANCELLED: frozenset(),
    WorkflowState.ROLLBACK_REQUIRED: frozenset({WorkflowState.ROLLED_BACK}),
    WorkflowState.ROLLED_BACK: frozenset(),
}


def assert_transition_allowed(current: WorkflowState, target: WorkflowState) -> None:
    if target not in ALLOWED_TRANSITIONS[current]:
        raise InvalidTransitionError(f"transition {current.value} -> {target.value} is forbidden")
