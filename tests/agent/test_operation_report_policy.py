from opensprite.agent.execution import ExecutionResult
from opensprite.agent.quality_gate import (
    OPERATION_VALIDATION_OR_RISK_MISSING_REASON,
    execution_confuses_command_version_with_repo_state,
    execution_has_failed_command_evidence,
    is_command_execution_tool_name,
    is_operations_task_type,
)
from opensprite.tools.evidence import ToolEvidence


def test_operation_validation_or_risk_missing_reason_is_stable():
    assert OPERATION_VALIDATION_OR_RISK_MISSING_REASON == "operation validation or risk was not reported"


def test_operation_report_policy_classifies_operation_task_and_execution_tools():
    assert is_operations_task_type("operations") is True
    assert is_operations_task_type("web_research") is False
    assert is_command_execution_tool_name("exec") is True
    assert is_command_execution_tool_name("process") is True
    assert is_command_execution_tool_name("web_search") is False


def test_operation_report_policy_detects_failed_command_evidence():
    result = ExecutionResult(
        content="done",
        tool_evidence=(
            ToolEvidence(name="web_search", ok=False),
            ToolEvidence(name="exec", ok=False),
        ),
    )

    assert execution_has_failed_command_evidence(result) is True


def test_operation_report_policy_detects_repo_state_version_confusion():
    confused = ExecutionResult(
        content="done",
        tool_evidence=(
            ToolEvidence(
                name="exec",
                ok=True,
                metadata={"tool_args": {"command": "git rev-parse HEAD"}},
            ),
        ),
    )
    direct = ExecutionResult(
        content="done",
        tool_evidence=(
            ToolEvidence(
                name="exec",
                ok=True,
                metadata={"tool_args": {"command": "git --version"}},
            ),
        ),
    )

    assert execution_confuses_command_version_with_repo_state(confused) is True
    assert execution_confuses_command_version_with_repo_state(direct) is False
