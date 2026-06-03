from opensprite.agent.completion_gate_policy import (
    INTERNAL_ONLY_RESPONSE_INCOMPLETE_REASON,
    MAX_TOOL_ITERATIONS_ACTIVE_TASK_DETAIL,
    MAX_TOOL_ITERATIONS_INCOMPLETE_REASON,
    TOOL_ERROR_WITHOUT_BLOCKER_REASON,
)


def test_max_tool_iterations_completion_gate_policy_is_stable():
    assert MAX_TOOL_ITERATIONS_INCOMPLETE_REASON == "max tool iterations exhausted before completion"
    assert "max_tool_iterations" in MAX_TOOL_ITERATIONS_ACTIVE_TASK_DETAIL


def test_internal_only_response_completion_gate_reason_is_stable():
    assert INTERNAL_ONLY_RESPONSE_INCOMPLETE_REASON == "assistant only emitted internal control text"


def test_tool_error_without_blocker_reason_is_stable():
    assert TOOL_ERROR_WITHOUT_BLOCKER_REASON == "tool execution reported an error without a clear blocker handoff"
