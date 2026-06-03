from opensprite.agent.completion_gate_policy import (
    INTERNAL_ONLY_RESPONSE_INCOMPLETE_REASON,
    MAX_TOOL_ITERATIONS_ACTIVE_TASK_DETAIL,
    MAX_TOOL_ITERATIONS_INCOMPLETE_REASON,
    PLAIN_ANSWER_CONTRACT_COMPLETE_REASON,
    TASK_CONTRACT_ACCEPTED_FINAL_RESPONSE_REASON,
    TOOL_ERROR_WITHOUT_BLOCKER_REASON,
)


def test_max_tool_iterations_completion_gate_policy_is_stable():
    assert MAX_TOOL_ITERATIONS_INCOMPLETE_REASON == "max tool iterations exhausted before completion"
    assert "max_tool_iterations" in MAX_TOOL_ITERATIONS_ACTIVE_TASK_DETAIL


def test_internal_only_response_completion_gate_reason_is_stable():
    assert INTERNAL_ONLY_RESPONSE_INCOMPLETE_REASON == "assistant only emitted internal control text"


def test_tool_error_without_blocker_reason_is_stable():
    assert TOOL_ERROR_WITHOUT_BLOCKER_REASON == "tool execution reported an error without a clear blocker handoff"


def test_plain_answer_contract_complete_reason_is_stable():
    assert PLAIN_ANSWER_CONTRACT_COMPLETE_REASON == "plain-answer contract received a response"


def test_task_contract_accepted_final_response_reason_is_stable():
    assert TASK_CONTRACT_ACCEPTED_FINAL_RESPONSE_REASON == "task contract accepted final response"
