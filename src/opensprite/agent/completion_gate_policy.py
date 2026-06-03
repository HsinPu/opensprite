"""Shared deterministic completion-gate failure policy."""

from __future__ import annotations


MAX_TOOL_ITERATIONS_INCOMPLETE_REASON = "max tool iterations exhausted before completion"
MAX_TOOL_ITERATIONS_ACTIVE_TASK_DETAIL = (
    "The execution loop hit the configured max_tool_iterations limit and needs another bounded continuation pass."
)
INTERNAL_ONLY_RESPONSE_INCOMPLETE_REASON = "assistant only emitted internal control text"
TOOL_ERROR_WITHOUT_BLOCKER_REASON = "tool execution reported an error without a clear blocker handoff"
PLAIN_ANSWER_CONTRACT_COMPLETE_REASON = "plain-answer contract received a response"
TASK_CONTRACT_ACCEPTED_FINAL_RESPONSE_REASON = "task contract accepted final response"
REQUIRED_FILE_CHANGES_AND_EVIDENCE_RECORDED_REASON = "required file changes and evidence were recorded"
GENERIC_TASK_COMPLETE_REASON = "generic task returned a response"
ANALYSIS_TASK_COMPLETE_REASON = "analysis-style task returned a substantive response"
EXPECTED_CODE_CHANGES_MISSING_REASON = "expected code changes were not recorded"
ONE_TURN_RESPONSE_COMPLETE_REASON = "one-turn intent received a response"
EMPTY_ASSISTANT_RESPONSE_REASON = "assistant response was empty"
TASK_CONTRACT_SATISFIED_REASON = "task contract was satisfied"


def one_turn_completion_reason(*, has_response: bool) -> str:
    return ONE_TURN_RESPONSE_COMPLETE_REASON if has_response else EMPTY_ASSISTANT_RESPONSE_REASON
