from opensprite.agent.completion_gate import CompletionGateResult
from opensprite.agent.completion.source_finalization import (
    source_finalization_allowed,
    task_contract_requires_web_sources,
)
from opensprite.agent.execution import ExecutionResult
from opensprite.agent.task.contract import AcceptanceCriterion, EvidenceRequirement, TaskContract


def test_source_finalization_policy_requires_nonfinal_web_contract():
    web_contract = TaskContract(objective="Find sources.", task_type="web_research")
    plain_contract = TaskContract(objective="Answer plainly.", task_type="pure_answer")

    assert source_finalization_allowed(
        CompletionGateResult(status="incomplete", reason="needs sources"),
        ExecutionResult(content="", task_contract=web_contract),
    )
    assert source_finalization_allowed(
        CompletionGateResult(status="blocked", reason="empty final answer"),
        ExecutionResult(content="", task_contract=web_contract),
    )
    assert source_finalization_allowed(
        CompletionGateResult(status="needs_review", reason="ungrounded citation"),
        ExecutionResult(content="", task_contract=web_contract),
    )
    assert not source_finalization_allowed(
        CompletionGateResult(status="complete", reason="answered"),
        ExecutionResult(content="", task_contract=web_contract),
    )
    assert not source_finalization_allowed(
        CompletionGateResult(status="waiting_user", reason="need user input"),
        ExecutionResult(content="", task_contract=web_contract),
    )
    assert not source_finalization_allowed(
        CompletionGateResult(status="incomplete", reason="needs sources"),
        ExecutionResult(content="", task_contract=plain_contract),
    )


def test_source_finalization_policy_detects_source_requirements_and_criteria():
    assert task_contract_requires_web_sources(
        TaskContract(
            objective="Find sources.",
            task_type="pure_answer",
            requirements=(EvidenceRequirement(kind="required_tool", tools=("web_search", "web_fetch", "web_research")),),
        )
    )
    assert task_contract_requires_web_sources(
        TaskContract(
            objective="Cite sources.",
            task_type="pure_answer",
            acceptance_criteria=(AcceptanceCriterion(kind="source_reference"),),
        )
    )
