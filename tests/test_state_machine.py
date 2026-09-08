import pytest

from control_plane.domain import InvalidTransitionError, WorkflowState
from control_plane.state_machine import ALLOWED_TRANSITIONS, assert_transition_allowed


def test_expected_happy_path_transitions_are_allowed() -> None:
    assert_transition_allowed(WorkflowState.CREATED, WorkflowState.PLANNING)
    assert_transition_allowed(WorkflowState.IMPLEMENTING, WorkflowState.TESTING)
    assert_transition_allowed(WorkflowState.CODE_REVIEW, WorkflowState.AWAITING_HUMAN_APPROVAL)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (WorkflowState.IMPLEMENTING, WorkflowState.DEPLOYED),
        (WorkflowState.TESTING, WorkflowState.APPROVED),
        (WorkflowState.CREATED, WorkflowState.MERGED),
        (WorkflowState.APPROVED, WorkflowState.DEPLOYED),
        (WorkflowState.CANCELLED, WorkflowState.PLANNING),
    ],
)
def test_forbidden_transitions_fail(current: WorkflowState, target: WorkflowState) -> None:
    with pytest.raises(InvalidTransitionError, match="forbidden"):
        assert_transition_allowed(current, target)


def test_all_states_have_an_explicit_transition_set() -> None:
    assert set(ALLOWED_TRANSITIONS) == set(WorkflowState)
