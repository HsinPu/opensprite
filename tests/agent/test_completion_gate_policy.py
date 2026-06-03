from opensprite.agent.completion_gate_policy import (
    ANALYSIS_TASK_COMPLETE_REASON,
    EMPTY_ASSISTANT_RESPONSE_REASON,
    EXPECTED_CODE_CHANGES_MISSING_REASON,
    GENERIC_TASK_COMPLETE_REASON,
    INTERNAL_ONLY_RESPONSE_INCOMPLETE_REASON,
    MAX_TOOL_ITERATIONS_ACTIVE_TASK_DETAIL,
    MAX_TOOL_ITERATIONS_INCOMPLETE_REASON,
    ONE_TURN_RESPONSE_COMPLETE_REASON,
    PLAIN_ANSWER_CONTRACT_COMPLETE_REASON,
    TASK_CONTRACT_ACCEPTED_FINAL_RESPONSE_REASON,
    TASK_CONTRACT_SATISFIED_REASON,
    TOOL_ERROR_WITHOUT_BLOCKER_REASON,
    one_turn_completion_reason,
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


def test_generic_task_complete_reason_is_stable():
    assert GENERIC_TASK_COMPLETE_REASON == "generic task returned a response"


def test_analysis_task_complete_reason_is_stable():
    assert ANALYSIS_TASK_COMPLETE_REASON == "analysis-style task returned a substantive response"


def test_expected_code_changes_missing_reason_is_stable():
    assert EXPECTED_CODE_CHANGES_MISSING_REASON == "expected code changes were not recorded"


def test_one_turn_completion_reason_reflects_response_presence():
    assert ONE_TURN_RESPONSE_COMPLETE_REASON == "one-turn intent received a response"
    assert EMPTY_ASSISTANT_RESPONSE_REASON == "assistant response was empty"
    assert one_turn_completion_reason(has_response=True) == ONE_TURN_RESPONSE_COMPLETE_REASON
    assert one_turn_completion_reason(has_response=False) == EMPTY_ASSISTANT_RESPONSE_REASON


def test_task_contract_satisfied_reason_is_stable():
    assert TASK_CONTRACT_SATISFIED_REASON == "task contract was satisfied"
